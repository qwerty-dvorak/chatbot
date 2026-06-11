"""Parameter-driven text ingestion pipeline.

Chunks -> embeds -> persists into the *chatbot-service* PostgreSQL database
(Django ``knowledge`` app tables).  Embedding parameters and HyDE questions
are stored in each chunk's ``metadata`` JSON field so the knowledge base is
fully reproducible.  Raw document bytes go into the object store; vectors go
to Milvus.

The live chatbot-service server only ever references records by their UUID.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from .config import cfg
from .models import Chunk, ChunkType, EmbeddedChunk, RawDocument
from .chunk import chunk as chunk_doc
from .embed import embed_text
from .index import connect_milvus, index_chunks
from .query import hypothetical_questions_for_chunk
from . import db as pgdb
from . import object_store as store

logger = logging.getLogger(__name__)


@dataclass
class TextPipelineParams:
    chunk_strategy: str = "recursive"
    chunk_size: int = 512
    chunk_overlap: int = 64
    parent_chunk_size: int = 2048
    sentence_window_size: int = 3
    embedding_model: str = ""
    embedding_dim: int = 0
    generate_hyde: bool = True
    hyde_per_chunk: int = 3
    milvus_collection: str = ""


def _resolve(params: Optional[dict] = None) -> TextPipelineParams:
    p = TextPipelineParams()
    if params is None:
        params = {}
    p.chunk_strategy = params.get("chunk_strategy", cfg.chunk_strategy)
    p.chunk_size = params.get("chunk_size", cfg.chunk_size)
    p.chunk_overlap = params.get("chunk_overlap", cfg.chunk_overlap)
    p.parent_chunk_size = params.get("parent_chunk_size", cfg.parent_chunk_size)
    p.sentence_window_size = params.get("sentence_window_size", cfg.sentence_window_size)
    p.embedding_model = params.get("embedding_model", cfg.text_embedding_model)
    p.embedding_dim = params.get("embedding_dim", cfg.text_embedding_dim)
    p.generate_hyde = params.get("generate_hyde", True)
    p.hyde_per_chunk = params.get("hyde_per_chunk", cfg.hypothetical_questions_per_chunk)
    p.milvus_collection = params.get("milvus_collection", cfg.text_collection)
    return p


def _patch_config(p: TextPipelineParams) -> None:
    cfg.chunk_strategy = p.chunk_strategy
    cfg.chunk_size = p.chunk_size
    cfg.chunk_overlap = p.chunk_overlap
    cfg.parent_chunk_size = p.parent_chunk_size
    cfg.sentence_window_size = p.sentence_window_size
    cfg.text_embedding_model = p.embedding_model
    cfg.text_embedding_dim = p.embedding_dim
    cfg.hypothetical_questions_per_chunk = p.hyde_per_chunk
    cfg.text_collection = p.milvus_collection


def _embed_params(p: TextPipelineParams) -> dict:
    return {
        "embedding_model": p.embedding_model,
        "embedding_dim": p.embedding_dim,
        "chunk_strategy": p.chunk_strategy,
        "chunk_size": p.chunk_size,
        "chunk_overlap": p.chunk_overlap,
    }


def process_document(
    doc: RawDocument,
    params: Optional[dict] = None,
) -> dict:
    """Run the full text ingestion pipeline on one document.

    Steps
    -----
    1. Store raw bytes in object store.
    2. Insert a Document row in Django's ``knowledge_documents`` table.
    3. Chunk text according to *params*.
    4. For each chunk: store chunk metadata (embedding params + HyDE
       questions) in the ``metadata`` JSONB field.
    5. Embed and index vectors into Milvus.
    6. Return stats.

    Args:
        doc:    Extracted raw document.
        params: Pipeline parameters; missing keys fall back to ``cfg``.

    Returns:
        Stats dict with ``document_id``, ``chunks_created``,
        ``embeddings_indexed``, ``hyde_generated``, ``object_key``.
    """
    p = _resolve(params)
    _patch_config(p)

    # 1. Object store
    object_key = store.store_file(doc.path)

    # 2. PostgreSQL document record (Django knowledge.documents table)
    pgdb.connect()
    doc_id = pgdb.insert_document(
        title=doc.metadata.get("filename", doc.path),
        mime_type=doc.content_type.value,
        original_filename=doc.metadata.get("filename", ""),
        extracted_text=doc.text,
        metadata={"source_path": doc.path, "object_key": object_key, **doc.metadata},
    )

    # 3. Chunk
    chunks: list[Chunk] = chunk_doc(doc, p.chunk_strategy)
    text_chunks = [c for c in chunks if c.image_data is None]

    # 4. Build chunk rows with embedding params + HyDE in metadata JSON
    embed_params = _embed_params(p)
    chunk_rows = []
    hyde_count = 0

    for idx, c in enumerate(text_chunks):
        chunk_meta = dict(c.metadata)
        chunk_meta["embedding_params"] = embed_params
        chunk_type_str = (
            c.chunk_type.value if hasattr(c.chunk_type, "value") else str(c.chunk_type)
        )
        chunk_meta["chunk_type"] = chunk_type_str

        # Generate HyDE questions -> store inside chunk metadata
        hyde_questions = []
        if p.generate_hyde and p.hyde_per_chunk > 0 and c.text.strip():
            questions = hypothetical_questions_for_chunk(c.text, n=p.hyde_per_chunk)
            hyde_questions = [q for q in questions if q.strip()]
            hyde_count += len(hyde_questions)

        chunk_meta["hyde_questions"] = hyde_questions
        chunk_meta["parent_id"] = c.parent_id or ""
        if c.window_text:
            chunk_meta["window_text"] = c.window_text

        chunk_rows.append({
            "chunk_index": idx,
            "content": c.text,
            "metadata": chunk_meta,
            "document_id": doc_id,
        })

    chunk_ids = pgdb.insert_chunks_batch(chunk_rows) if chunk_rows else []

    # 5. Embed + index into Milvus
    connect_milvus()
    text_embedded: list[EmbeddedChunk] = embed_text(text_chunks)
    if text_embedded:
        index_chunks(text_embedded)

    return {
        "document_id": doc_id,
        "object_key": object_key,
        "chunks_created": len(text_chunks),
        "embeddings_indexed": len(text_embedded),
        "hyde_generated": hyde_count,
    }
