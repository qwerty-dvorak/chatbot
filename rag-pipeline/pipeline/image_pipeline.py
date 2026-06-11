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
) -> dict:
    """Run the full image ingestion pipeline on one document.

    Steps
    -----
    1. Store raw image bytes in object store.
    2. Insert Document + DocumentAsset rows in Django tables.
    3. Create image chunks, store chunk metadata in ``document_chunks``.
    4. Embed via multimodal model and index into Milvus.

    Args:
        doc:    Extracted raw document (must have ``images``).
        params: Overrides for ``embedding_model``, ``embedding_dim``,
                ``milvus_collection``.

    Returns:
        Stats dict with ``document_id``, ``chunks_created``,
        ``embeddings_indexed``, ``object_key``.
    """
    p = _resolve(params)
    cfg.multimodal_embedding_model = p.embedding_model
    cfg.multimodal_embedding_dim = p.embedding_dim
    cfg.image_collection = p.milvus_collection

    # 1. Object store
    object_key = store.store_file(doc.path)

    # 2. PostgreSQL document record
    pgdb.connect()
    doc_id = pgdb.insert_document(
        title=doc.metadata.get("filename", doc.path),
        mime_type=doc.content_type.value,
        original_filename=doc.metadata.get("filename", ""),
        metadata={"source_path": doc.path, "object_key": object_key, **doc.metadata},
    )

    # 3. Image chunks + asset rows
    image_chunks: list[Chunk] = []
    chunk_rows = []
    embed_params = {
        "embedding_model": p.embedding_model,
        "embedding_dim": p.embedding_dim,
    }

    for i, image_bytes in enumerate(doc.images):
        chunk_id = uuid.uuid4().hex

        # DocumentAsset row (one per image/page)
        asset_id = pgdb.insert_asset(
            document_id=doc_id,
            asset_type="image",
            page_number=i,
            metadata={"image_index": i, "object_key": object_key},
        )

        chunk_meta = {
            "embedding_params": embed_params,
            "chunk_type": "image",
            "is_image": True,
            "image_index": i,
            "has_image_data": True,
        }

        chunk_rows.append({
            "chunk_index": i,
            "content": "",
            "metadata": chunk_meta,
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

    # 4. Embed via multimodal + index into Milvus
    connect_milvus()
    embedded: list[EmbeddedChunk] = embed_multimodal(image_chunks)
    if embedded:
        index_chunks(embedded)

    return {
        "document_id": doc_id,
        "object_key": object_key,
        "chunks_created": len(image_chunks),
        "embeddings_indexed": len(embedded),
    }
