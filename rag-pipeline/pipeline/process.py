"""Document-level ingestion — used by the API job worker."""

import hashlib
import logging
import time
import uuid

from . import db as pgdb
from . import object_store as store
from .chunk import chunk as chunk_doc
from .config import cfg
from .embed import embed_multimodal, embed_text
from .image_preprocess import preprocess_images
from .milvus import connect_milvus, index_chunks
from .models import Chunk, ChunkType, RawDocument
from .ocr import ocr_pages_detailed, validate_mode
from .query import _chat, hypothetical_questions_for_chunk

logger = logging.getLogger(__name__)


def process_text(doc: RawDocument, params: dict | None = None) -> dict:  # noqa: PLR0915
    """Process a text document: chunk, embed, index."""
    p = params or {}
    strategy = p.get("chunk_strategy", cfg.chunk_strategy)
    hyde = p.get("generate_hyde", False)
    hyde_n = int(p.get("hyde_per_chunk", cfg.hypothetical_questions_per_chunk))
    gen_summary = p.get("generate_summary", True)
    existing_doc_id = p.get("existing_document_id")
    existing_object_key = p.get("existing_object_key", "")
    chunk_offset = int(p.get("chunk_index_offset", 0))
    source_name = doc.metadata.get("filename", doc.path)
    timing: dict[str, float] = {}
    _t = time.time

    t0 = _t()
    object_key = existing_object_key or store.store_file(doc.path)
    timing["store_raw"] = round(_t() - t0, 4)

    t0 = _t()
    pgdb.connect()
    if existing_doc_id:
        pgdb.update_document(
            doc_id=existing_doc_id,
            extracted_text=doc.text,
            status="ready",
            metadata={"source_path": doc.path, "object_key": object_key, **doc.metadata},
        )
        doc_id = existing_doc_id
    else:
        doc_id = pgdb.insert_document(
            title=source_name,
            mime_type=doc.content_type.value,
            original_filename=doc.metadata.get("filename", ""),
            extracted_text=doc.text,
            metadata={"source_path": doc.path, "object_key": object_key, **doc.metadata},
        )
    timing["db_insert"] = round(_t() - t0, 4)

    t0 = _t()
    chunks = chunk_doc(doc, strategy)
    text_chunks = [c for c in chunks if c.image_data is None]
    timing["chunk"] = round(_t() - t0, 4)

    summary_chunk = None
    if gen_summary and doc.text.strip():
        t0 = _t()
        summary_text = _chat(
            "You are an expert at summarising documents. Given a document, produce a concise summary (3-5 sentences) "
            "that captures the key topics, themes, and findings. Output only the summary text, nothing else.",
            f"Document:\n{doc.text[:8000]}",
        ).strip()
        if summary_text:
            summary_chunk = Chunk(
                id=uuid.uuid4().hex, source_path=doc.path, text=summary_text, chunk_type=ChunkType.SUMMARY, metadata=dict(doc.metadata)
            )
            pgdb.execute("UPDATE documents SET analysis_summary = %s, updated_at = NOW() WHERE id = %s", (summary_text, doc_id))
        timing["summary"] = round(_t() - t0, 4)

    t0 = _t()
    hyde_count = 0
    question_chunks: list[Chunk] = []
    embed_params = {
        "embedding_model": cfg.text_embedding_model,
        "embedding_dim": cfg.text_embedding_dim,
        "chunk_strategy": strategy,
        "chunk_size": cfg.chunk_size,
        "chunk_overlap": cfg.chunk_overlap,
    }
    chunk_rows: list[dict] = []
    for idx, c in enumerate(text_chunks):
        meta = dict(c.metadata)
        meta["embedding_params"] = embed_params
        meta["chunk_type"] = c.chunk_type.value if hasattr(c.chunk_type, "value") else str(c.chunk_type)
        questions = hypothetical_questions_for_chunk(c.text, n=hyde_n) if hyde and hyde_n > 0 and c.text.strip() else []
        hyde_count += len(questions)
        question_chunks.extend(
            Chunk(
                id=uuid.uuid4().hex,
                source_path=c.source_path,
                text=q_text,
                chunk_type=ChunkType.HYPOTHETICAL_QUESTION,
                parent_id=c.id,
                metadata={"chunk_type": ChunkType.HYPOTHETICAL_QUESTION.value, "source_chunk_id": c.id, "chunk_index": idx},
            )
            for q_text in questions
            if q_text.strip()
        )
        meta["hyde_questions"] = questions
        meta["parent_id"] = c.parent_id or ""
        if c.window_text:
            meta["window_text"] = c.window_text
        chunk_rows.append({"chunk_index": chunk_offset + idx, "content": c.text, "metadata": meta, "document_id": doc_id})
    timing["hyde"] = round(_t() - t0, 4)

    t0 = _t()
    if chunk_rows:
        pgdb.insert_chunks_batch(chunk_rows)
    timing["persist"] = round(_t() - t0, 4)

    t0 = _t()
    all_embed = text_chunks + question_chunks
    if summary_chunk:
        all_embed.append(summary_chunk)
    connect_milvus()
    text_embedded = embed_text(all_embed)
    timing["embed"] = round(_t() - t0, 4)

    t0 = _t()
    if text_embedded:
        index_chunks(text_embedded)
    timing["index"] = round(_t() - t0, 4)

    timing["total"] = round(sum(v for v in timing.values()), 4)
    return {
        "document_id": doc_id,
        "object_key": object_key,
        "chunks_created": len(text_chunks),
        "embeddings_indexed": len(text_embedded),
        "hyde_generated": hyde_count,
        "question_chunks_indexed": len(question_chunks),
        "summary_indexed": 1 if summary_chunk else 0,
        "summary_text": summary_chunk.text if summary_chunk else "",
        "embedding_info": {
            "model": cfg.text_embedding_model,
            "dimension": cfg.text_embedding_dim,
            "collection": cfg.text_collection,
            "chunk_strategy": strategy,
        },
        "timing": timing,
    }


def process_image(doc: RawDocument, params: dict | None = None) -> dict:
    """Process an image document: OCR, embed, index."""
    p = params or {}
    ocr_mode = validate_mode(p.get("ocr_mode"))
    use_mm = p.get("use_multimodal_embedding", True)
    use_text = p.get("use_text_embedding", True)
    strategy = p.get("chunk_strategy") or cfg.chunk_strategy
    hyde = p.get("generate_hyde", False)
    existing_doc_id = p.get("existing_document_id")
    max_h = int(p.get("max_image_height", cfg.ocr_max_image_height))
    source_name = doc.metadata.get("filename", doc.path)

    object_key = store.store_file(doc.path)
    prepared = preprocess_images(doc.images, max_h)
    derived_keys = [store.store(item.data, suffix=".png") for item in prepared]
    ocr_results = ocr_pages_detailed([item.data for item in prepared], mode=ocr_mode)
    ocr_texts = [r.text for r in ocr_results]
    combined = "\n\n".join(t for t in (doc.text.strip(), "\n\n".join(t for t in ocr_texts if t.strip())) if t)

    pgdb.connect()
    meta = {
        "source_path": doc.path,
        "object_key": object_key,
        "source_image_count": len(doc.images),
        "derived_image_count": len(prepared),
        **doc.metadata,
    }
    if existing_doc_id:
        pgdb.update_document(doc_id=existing_doc_id, extracted_text=combined, status="ready", ocr_mode=ocr_mode, metadata=meta)
        doc_id = existing_doc_id
    else:
        doc_id = pgdb.insert_document(
            title=source_name,
            mime_type=doc.content_type.value,
            original_filename=source_name,
            sha256=object_key,
            extracted_text=combined,
            ocr_mode=ocr_mode,
            metadata=meta,
        )

    image_chunks: list[Chunk] = []
    chunk_rows: list[dict] = []
    for idx, (item, key, ocr_result) in enumerate(zip(prepared, derived_keys, ocr_results, strict=True)):
        asset_id = pgdb.insert_asset(
            document_id=doc_id,
            asset_type="page" if doc.content_type.value == "pdf" else "image",
            file=store.store_path(key) or "",
            mime_type="image/png",
            page_number=item.source_index + 1,
            text=ocr_result.text,
            source_index=item.source_index,
            derived_index=item.derived_index,
            object_key=key,
            sha256=hashlib.sha256(item.data).hexdigest(),
            width=item.width,
            height=item.height,
            ocr_backend=ocr_mode,
            ocr_status=ocr_result.status,
            preprocessing={"operations": list(item.operations)},
            metadata={"raw_object_key": object_key, "ocr_error": ocr_result.error},
        )
        chunk = Chunk(
            id=uuid.uuid4().hex,
            source_path=doc.path,
            text=ocr_result.text,
            chunk_type=ChunkType.IMAGE,
            metadata={
                **doc.metadata,
                "document_id": doc_id,
                "asset_id": asset_id,
                "source_index": item.source_index,
                "derived_index": item.derived_index,
                "object_key": key,
                "ocr_mode": ocr_mode,
            },
            image_data=item.data,
        )
        image_chunks.append(chunk)
        chunk_rows.append(
            {
                "document_id": doc_id,
                "asset_id": asset_id,
                "chunk_index": idx,
                "content": ocr_result.text,
                "metadata": {**chunk.metadata, "chunk_type": ChunkType.IMAGE.value},
            }
        )
    if chunk_rows:
        pgdb.insert_chunks_batch(chunk_rows)

    embedded = []
    if use_mm and image_chunks:
        connect_milvus()
        try:
            embedded = embed_multimodal(image_chunks)
        except BaseException:
            logger.exception("Multimodal embedding failed; continuing without image vectors")
    if embedded:
        index_chunks(embedded)

    text_result = None
    if use_text and combined.strip():
        text_doc = RawDocument(
            path=doc.path,
            content_type=doc.content_type,
            text=combined,
            images=[],
            metadata={**doc.metadata, "ocr_mode": ocr_mode, "image_document_id": doc_id},
        )
        text_result = process_text(
            text_doc,
            {
                "existing_document_id": doc_id,
                "existing_object_key": object_key,
                "chunk_index_offset": len(chunk_rows),
                "chunk_strategy": strategy,
                "generate_hyde": hyde,
                "generate_summary": False,
            },
        )
    return {
        "document_id": doc_id,
        "object_key": object_key,
        "source_images": len(doc.images),
        "derived_images": len(prepared),
        "chunks_created": len(image_chunks) + (text_result or {}).get("chunks_created", 0),
        "image_embeddings_indexed": len(embedded),
        "text_embeddings_indexed": (text_result or {}).get("embeddings_indexed", 0),
        "embeddings_indexed": len(embedded) + (text_result or {}).get("embeddings_indexed", 0),
        "ocr_mode": ocr_mode,
        "ocr_characters": len(combined),
        "embedding_info": {
            "model": p.get("embedding_model", cfg.multimodal_embedding_model),
            "dimension": p.get("embedding_dim", cfg.multimodal_embedding_dim),
            "collection": p.get("milvus_collection", cfg.image_collection),
        },
    }
