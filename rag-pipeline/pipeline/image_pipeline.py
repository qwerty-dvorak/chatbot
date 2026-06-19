"""Image/PDF ingestion with selectable OCR and dual image/text indexing."""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from . import db as pgdb
from . import object_store as store
from .config import cfg
from .embed import embed_multimodal
from .image_preprocess import preprocess_images
from .index import connect_milvus, index_chunks
from .models import Chunk, ChunkType, ContentType, EmbeddedChunk, RawDocument
from .ocr import ocr_pages_detailed, validate_mode
from .text_pipeline import process_document as process_text_document

logger = logging.getLogger(__name__)


@dataclass
class ImagePipelineParams:
    embedding_model: str = ""
    embedding_dim: int = 0
    milvus_collection: str = ""
    ocr_mode: str = ""
    max_image_height: int = 0
    use_multimodal_embedding: bool = True
    use_text_embedding: bool = True
    chunk_strategy: str = ""
    generate_hyde: bool = False


def _resolve(params: Optional[dict] = None) -> ImagePipelineParams:
    values = params or {}
    return ImagePipelineParams(
        embedding_model=values.get("embedding_model", cfg.multimodal_embedding_model),
        embedding_dim=values.get("embedding_dim", cfg.multimodal_embedding_dim),
        milvus_collection=values.get("milvus_collection", cfg.image_collection),
        ocr_mode=validate_mode(values.get("ocr_mode")),
        max_image_height=int(values.get("max_image_height", cfg.ocr_max_image_height)),
        use_multimodal_embedding=values.get("use_multimodal_embedding", True),
        use_text_embedding=values.get("use_text_embedding", True),
        chunk_strategy=values.get("chunk_strategy") or cfg.chunk_strategy,
        generate_hyde=values.get("generate_hyde", False),
    )


def process_document(doc: RawDocument, params: Optional[dict] = None, progress=None) -> dict:
    p = _resolve(params)
    cfg.multimodal_embedding_model = p.embedding_model
    cfg.multimodal_embedding_dim = p.embedding_dim
    cfg.image_collection = p.milvus_collection
    source_name = doc.metadata.get("filename", doc.path)

    if progress:
        progress.start("store_raw", f"storing {source_name}")
    object_key = store.store_file(doc.path)
    prepared = preprocess_images(doc.images, p.max_image_height)
    derived_keys = [store.store(item.data, suffix=".png") for item in prepared]
    if progress:
        progress.complete("store_raw", f"raw={object_key}; derived={len(derived_keys)}")

    if progress:
        progress.start("ocr", f"mode={p.ocr_mode}; images={len(prepared)}")
    ocr_results = ocr_pages_detailed([item.data for item in prepared], mode=p.ocr_mode)
    ocr_texts = [result.text for result in ocr_results]
    ocr_text = "\n\n".join(text for text in ocr_texts if text.strip())
    combined_text = "\n\n".join(text for text in (doc.text.strip(), ocr_text) if text)
    if progress:
        progress.complete("ocr", f"mode={p.ocr_mode}; chars={len(ocr_text)}")

    pgdb.connect()
    if progress:
        progress.start("db_insert", "creating document and per-image asset records")
    existing_doc_id = (params or {}).get("existing_document_id")
    if existing_doc_id:
        doc_id = existing_doc_id
        pgdb.update_document(
            doc_id=doc_id,
            extracted_text=combined_text,
            status="ready",
            ocr_mode=p.ocr_mode,
            metadata={
                "source_path": doc.path, "object_key": object_key,
                "source_image_count": len(doc.images), "derived_image_count": len(prepared),
                **doc.metadata,
            },
        )
    else:
        doc_id = pgdb.insert_document(
            title=source_name,
            mime_type=doc.content_type.value,
            original_filename=source_name,
            sha256=object_key,
            extracted_text=combined_text,
            ocr_mode=p.ocr_mode,
            metadata={
                "source_path": doc.path, "object_key": object_key,
                "source_image_count": len(doc.images), "derived_image_count": len(prepared),
                **doc.metadata,
            },
        )
    if progress:
        progress.complete("db_insert", f"document_id={doc_id}")

    if progress:
        progress.start("persist", f"persisting {len(prepared)} derived images")
    image_chunks: list[Chunk] = []
    chunk_rows: list[dict] = []
    for index, (item, key, ocr_result) in enumerate(zip(prepared, derived_keys, ocr_results)):
        text = ocr_result.text
        asset_id = pgdb.insert_asset(
            document_id=doc_id, asset_type="page" if doc.content_type == ContentType.PDF else "image",
            file=store.store_path(key) or "", mime_type="image/png",
            page_number=item.source_index + 1, text=text,
            source_index=item.source_index, derived_index=item.derived_index,
            object_key=key, sha256=hashlib.sha256(item.data).hexdigest(),
            width=item.width, height=item.height, ocr_backend=p.ocr_mode,
            ocr_status=ocr_result.status,
            preprocessing={"operations": list(item.operations)},
            metadata={"raw_object_key": object_key, "ocr_error": ocr_result.error},
        )
        chunk = Chunk(
            id=uuid.uuid4().hex, source_path=doc.path, text=text,
            chunk_type=ChunkType.IMAGE,
            metadata={
                **doc.metadata, "document_id": doc_id, "asset_id": asset_id,
                "source_index": item.source_index, "derived_index": item.derived_index,
                "object_key": key, "ocr_mode": p.ocr_mode,
            },
            image_data=item.data,
        )
        image_chunks.append(chunk)
        chunk_rows.append({
            "document_id": doc_id, "asset_id": asset_id, "chunk_index": index,
            "content": text,
            "metadata": {**chunk.metadata, "chunk_type": ChunkType.IMAGE.value},
        })

    if chunk_rows:
        pgdb.insert_chunks_batch(chunk_rows)
    if progress:
        progress.complete("persist", f"assets={len(prepared)}; image_chunks={len(chunk_rows)}")

    embedded: list[EmbeddedChunk] = []
    if progress:
        progress.start("embed", f"multimodal={p.use_multimodal_embedding}")
    if p.use_multimodal_embedding and image_chunks:
        connect_milvus()
        embedded = embed_multimodal(image_chunks)
    if progress:
        progress.complete("embed", f"image_vectors={len(embedded)}")
        progress.start("index", f"image_vectors={len(embedded)}")
    if embedded:
        index_chunks(embedded)
    if progress:
        progress.complete("index", f"image_vectors={len(embedded)}")

    text_result = None
    if p.use_text_embedding and combined_text.strip():
        if progress:
            progress.start("text_pipeline", "chunking and indexing extracted/OCR text")
        text_doc = RawDocument(
            path=doc.path, content_type=ContentType.TEXT, text=combined_text,
            images=[], metadata={**doc.metadata, "ocr_mode": p.ocr_mode, "image_document_id": doc_id},
        )
        text_result = process_text_document(text_doc, params={
            "existing_document_id": doc_id,
            "existing_object_key": object_key,
            "chunk_index_offset": len(chunk_rows),
            "chunk_strategy": p.chunk_strategy,
            "generate_hyde": p.generate_hyde,
            "generate_summary": False,
        })
        if progress:
            progress.complete("text_pipeline", f"chunks={text_result['chunks_created']}")

    if progress:
        for step, detail in (
            ("chunk", f"handled by text_pipeline; chunks={(text_result or {}).get('chunks_created', 0)}"),
            ("summary", "disabled for image OCR text"),
            ("hyde", f"generated={(text_result or {}).get('hyde_generated', 0)}"),
        ):
            progress.start(step, detail)
            progress.complete(step, detail)

    return {
        "document_id": doc_id,
        "object_key": object_key,
        "source_images": len(doc.images),
        "derived_images": len(prepared),
        "chunks_created": len(image_chunks) + (text_result or {}).get("chunks_created", 0),
        "image_embeddings_indexed": len(embedded),
        "text_embeddings_indexed": (text_result or {}).get("embeddings_indexed", 0),
        "embeddings_indexed": len(embedded) + (text_result or {}).get("embeddings_indexed", 0),
        "ocr_mode": p.ocr_mode,
        "ocr_characters": len(ocr_text),
        "embedding_info": {
            "model": p.embedding_model, "dimension": p.embedding_dim,
            "collection": p.milvus_collection,
        },
    }
