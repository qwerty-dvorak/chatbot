"""Parameter-driven image ingestion pipeline.

Takes an image document, creates image chunks, embeds via the multimodal
model, and persists into the *chatbot-service* PostgreSQL database (Django
``knowledge`` tables).  Raw image bytes go into the object store; vectors go
to Milvus.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from .config import cfg
from .models import Chunk, ChunkType, EmbeddedChunk, RawDocument
from .embed import embed_multimodal
from .index import connect_milvus, index_chunks
from . import db as pgdb
from . import object_store as store

logger = logging.getLogger(__name__)


@dataclass
class ImagePipelineParams:
    embedding_model: str = ""
    embedding_dim: int = 0
    milvus_collection: str = ""


def _resolve(params: Optional[dict] = None) -> ImagePipelineParams:
    p = ImagePipelineParams()
    if params is None:
        params = {}
    p.embedding_model = params.get("embedding_model", cfg.multimodal_embedding_model)
    p.embedding_dim = params.get("embedding_dim", cfg.multimodal_embedding_dim)
    p.milvus_collection = params.get("milvus_collection", cfg.image_collection)
    return p


def process_document(
    doc: RawDocument,
    params: Optional[dict] = None,
    progress: Optional["ProgressTracker"] = None,
) -> dict:
    """Run the full image ingestion pipeline on one document.

    Each step is logged to console and, if a ``ProgressTracker`` is provided,
    persisted to the job store for frontend visibility.

    Steps
    -----
    0. Resolve params
    1. store_raw  — store raw image bytes in object store
    2. db_insert  — create Document + DocumentAsset rows
    3. persist    — write image chunk rows to document_chunks table
    4. embed      — embed via multimodal model
    5. index      — index vectors into Milvus

    Args:
        doc:      Extracted raw document (must have ``images``).
        params:   Overrides for ``embedding_model``, ``embedding_dim``,
                  ``milvus_collection``.
        progress: Optional step tracker for frontend status reflection.

    Returns:
        Stats dict with ``document_id``, ``chunks_created``,
        ``embeddings_indexed``, ``object_key``.
    """
    p = _resolve(params)
    cfg.multimodal_embedding_model = p.embedding_model
    cfg.multimodal_embedding_dim = p.embedding_dim
    cfg.image_collection = p.milvus_collection

    log = logger.info
    source_name = doc.metadata.get("filename", doc.path)

    # Step 0 — param summary
    log("[image_pipeline] source=%s model=%s dim=%d images=%d",
        source_name, p.embedding_model, p.embedding_dim, len(doc.images))
    print(f"[image_pipeline] source={source_name} model={p.embedding_model} "
          f"dim={p.embedding_dim} images={len(doc.images)}")

    # Step 1 — object store
    if progress:
        progress.start("store_raw", f"storing {source_name}")
    log("[image_pipeline] step=store_raw source=%s", source_name)
    print(f"[image_pipeline] step=store_raw storing {source_name}")
    object_key = store.store_file(doc.path)
    log("[image_pipeline] step=store_raw key=%s", object_key)
    print(f"[image_pipeline] step=store_raw key={object_key}")
    if progress:
        progress.complete("store_raw", f"key={object_key}")

    # Step 2 — PostgreSQL Document + DocumentAsset rows
    if progress:
        progress.start("db_insert", "creating Document + DocumentAsset rows")
    log("[image_pipeline] step=db_insert inserting document source=%s", source_name)
    print(f"[image_pipeline] step=db_insert source={source_name}")
    pgdb.connect()
    doc_id = pgdb.insert_document(
        title=doc.metadata.get("filename", doc.path),
        mime_type=doc.content_type.value,
        original_filename=doc.metadata.get("filename", ""),
        metadata={"source_path": doc.path, "object_key": object_key, **doc.metadata},
    )
    log("[image_pipeline] step=db_insert doc_id=%s", doc_id)
    print(f"[image_pipeline] step=db_insert doc_id={doc_id}")
    if progress:
        progress.complete("db_insert", f"doc_id={doc_id}")

    # Step 3 — image chunks + asset rows
    if progress:
        progress.start("persist", "creating image chunk rows")
    image_chunks: list[Chunk] = []
    chunk_rows = []
    embed_params = {
        "embedding_model": p.embedding_model,
        "embedding_dim": p.embedding_dim,
    }
    log("[image_pipeline] step=persist images=%d", len(doc.images))
    print(f"[image_pipeline] step=persist creating {len(doc.images)} image chunks")

    for i, image_bytes in enumerate(doc.images):
        chunk_id = uuid.uuid4().hex
        asset_id = pgdb.insert_asset(
            document_id=doc_id,
            asset_type="image",
            page_number=i,
            metadata={"image_index": i, "object_key": object_key},
        )
        chunk_rows.append({
            "chunk_index": i,
            "content": "",
            "metadata": {
                "embedding_params": embed_params,
                "chunk_type": "image",
                "is_image": True,
                "image_index": i,
                "has_image_data": True,
            },
            "document_id": doc_id,
            "asset_id": asset_id,
        })
        chunk = Chunk(
            id=chunk_id,
            source_path=doc.path,
            text="",
            chunk_type=ChunkType.IMAGE,
            metadata={**doc.metadata, "is_image": True, "image_index": i},
            image_data=image_bytes,
        )
        image_chunks.append(chunk)

    if chunk_rows:
        pgdb.insert_chunks_batch(chunk_rows)
    log("[image_pipeline] step=persist %d chunk rows written", len(chunk_rows))
    print(f"[image_pipeline] step=persist {len(chunk_rows)} chunk rows")
    if progress:
        progress.complete("persist", f"{len(chunk_rows)} image chunks")

    # Step 4 — embed via multimodal model
    if progress:
        progress.start("embed", f"model={p.embedding_model}")
    log("[image_pipeline] step=embed model=%s dim=%d chunks=%d",
        p.embedding_model, p.embedding_dim, len(image_chunks))
    print(f"[image_pipeline] step=embed model={p.embedding_model} "
          f"dim={p.embedding_dim} chunks={len(image_chunks)}")
    connect_milvus()
    embedded: list[EmbeddedChunk] = embed_multimodal(image_chunks)
    log("[image_pipeline] step=embed embedded=%d", len(embedded))
    print(f"[image_pipeline] step=embed {len(embedded)} embeddings")
    if progress:
        progress.complete("embed", f"{len(embedded)} vectors")

    # Step 5 — index into Milvus
    if progress:
        progress.start("index", "indexing into Milvus")
    if embedded:
        log("[image_pipeline] step=index inserting %d vectors into Milvus collection=%s",
            len(embedded), cfg.image_collection)
        print(f"[image_pipeline] step=index collection={cfg.image_collection} "
              f"vectors={len(embedded)}")
        index_chunks(embedded)
    log("[image_pipeline] step=index done")
    print("[image_pipeline] step=index complete")
    if progress:
        progress.complete("index", f"{len(embedded)} vectors indexed")

    result = {
        "document_id": doc_id,
        "object_key": object_key,
        "chunks_created": len(image_chunks),
        "embeddings_indexed": len(embedded),
    }
    log("[image_pipeline] done doc_id=%s chunks=%d embeddings=%d",
        doc_id, len(image_chunks), len(embedded))
    print(f"[image_pipeline] done doc_id={doc_id} chunks={len(image_chunks)} "
          f"embeddings={len(embedded)}")
    return result
