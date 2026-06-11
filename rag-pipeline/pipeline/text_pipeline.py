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
import uuid
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
    progress: Optional["ProgressTracker"] = None,
) -> dict:
    """Run the full text ingestion pipeline on one document.

    Each step is logged to console and, if a ``ProgressTracker`` is provided,
    persisted to the job store for frontend visibility.

    Steps
    -----
    0. Resolve params
    1. store_raw  — store bytes in object store (SHA-256 key)
    2. db_insert  — create Document row in chatbot-service PostgreSQL
    3. chunk      — split text into chunks per strategy
    4. hyde       — generate hypothetical questions per chunk (optional)
    5. persist    — write chunk rows to document_chunks table
    6. embed      — embed all chunks via text embedding model
    7. index      — index vectors into Milvus

    Args:
        doc:      Extracted raw document.
        params:   Pipeline parameters; missing keys fall back to ``cfg``.
        progress: Optional step tracker for frontend status reflection.

    Returns:
        Stats dict with ``document_id``, ``chunks_created``,
        ``embeddings_indexed``, ``hyde_generated``, ``object_key``.
    """
    p = _resolve(params)
    _patch_config(p)

    log = logger.info
    source_name = doc.metadata.get("filename", doc.path)

    # Step 0 — param summary
    log("[text_pipeline] source=%s strategy=%s chunk_size=%d overlap=%d hyde=%s",
        source_name, p.chunk_strategy, p.chunk_size, p.chunk_overlap, p.generate_hyde)
    print(f"[text_pipeline] source={source_name} strategy={p.chunk_strategy} "
          f"chunk_size={p.chunk_size} overlap={p.chunk_overlap} hyde={p.generate_hyde}")

    # Step 1 — object store
    if progress:
        progress.start("store_raw", f"storing {source_name}")
    log("[text_pipeline] step=store_raw source=%s", source_name)
    print(f"[text_pipeline] step=store_raw storing {source_name}")
    object_key = store.store_file(doc.path)
    log("[text_pipeline] step=store_raw key=%s", object_key)
    print(f"[text_pipeline] step=store_raw key={object_key}")
    if progress:
        progress.complete("store_raw", f"key={object_key}")

    # Step 2 — PostgreSQL Document row
    if progress:
        progress.start("db_insert", "creating Document row")
    log("[text_pipeline] step=db_insert inserting document source=%s", source_name)
    print(f"[text_pipeline] step=db_insert source={source_name}")
    pgdb.connect()
    doc_id = pgdb.insert_document(
        title=doc.metadata.get("filename", doc.path),
        mime_type=doc.content_type.value,
        original_filename=doc.metadata.get("filename", ""),
        extracted_text=doc.text,
        metadata={"source_path": doc.path, "object_key": object_key, **doc.metadata},
    )
    log("[text_pipeline] step=db_insert doc_id=%s", doc_id)
    print(f"[text_pipeline] step=db_insert doc_id={doc_id}")
    if progress:
        progress.complete("db_insert", f"doc_id={doc_id}")

    # Step 3 — chunk
    if progress:
        progress.start("chunk", f"strategy={p.chunk_strategy}")
    log("[text_pipeline] step=chunk strategy=%s", p.chunk_strategy)
    print(f"[text_pipeline] step=chunk strategy={p.chunk_strategy}")
    chunks: list[Chunk] = chunk_doc(doc, p.chunk_strategy)
    text_chunks = [c for c in chunks if c.image_data is None]
    log("[text_pipeline] step=chunk chunks=%d text_chunks=%d",
        len(chunks), len(text_chunks))
    print(f"[text_pipeline] step=chunk total={len(chunks)} text={len(text_chunks)}")
    if progress:
        progress.complete("chunk", f"{len(text_chunks)} text chunks")

    # Step 4 — Hypothetical question generation
    hyde_count = 0
    question_chunks: list[Chunk] = []
    if progress:
        progress.start("hyde", "generating hypothetical questions")
    embed_params = _embed_params(p)
    chunk_rows = []
    for idx, c in enumerate(text_chunks):
        chunk_meta = dict(c.metadata)
        chunk_meta["embedding_params"] = embed_params
        chunk_type_str = (
            c.chunk_type.value if hasattr(c.chunk_type, "value") else str(c.chunk_type)
        )
        chunk_meta["chunk_type"] = chunk_type_str

        hyde_questions = []
        if p.generate_hyde and p.hyde_per_chunk > 0 and c.text.strip():
            questions = hypothetical_questions_for_chunk(c.text, n=p.hyde_per_chunk)
            hyde_questions = [q for q in questions if q.strip()]
            hyde_count += len(hyde_questions)
            if hyde_questions:
                log("[text_pipeline] step=hyde chunk=%s.. questions=%d",
                    c.id[:8], len(hyde_questions))

            # Create separate chunk objects for each question so they get
            # embedded as vectors and indexed into Milvus (hypothetical question
            # chunk type with parent_id pointing back to source chunk).
            for q_text in hyde_questions:
                q_chunk = Chunk(
                    id=uuid.uuid4().hex,
                    source_path=c.source_path,
                    text=q_text,
                    chunk_type=ChunkType.HYPOTHETICAL_QUESTION,
                    parent_id=c.id,
                    metadata={
                        "chunk_type": ChunkType.HYPOTHETICAL_QUESTION.value,
                        "source_chunk_id": c.id,
                        "chunk_index": idx,
                    },
                )
                question_chunks.append(q_chunk)

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

    log("[text_pipeline] step=hyde total_questions=%d question_chunks=%d",
        hyde_count, len(question_chunks))
    print(f"[text_pipeline] step=hyde generated {hyde_count} questions "
          f"({len(question_chunks)} vector chunks)")
    if progress:
        progress.complete("hyde", f"{hyde_count} questions")

    # Step 5 — persist chunks to PostgreSQL
    if progress:
        progress.start("persist", "writing to document_chunks")
    chunk_ids = pgdb.insert_chunks_batch(chunk_rows) if chunk_rows else []
    log("[text_pipeline] step=persist chunk_ids=%d written", len(chunk_ids))
    print(f"[text_pipeline] step=persist {len(chunk_ids)} chunks written")
    if progress:
        progress.complete("persist", f"{len(chunk_ids)} chunks")

    # Step 6 — embed (text chunks + hypothetical question chunks)
    all_to_embed = text_chunks + question_chunks
    if progress:
        progress.start("embed", f"model={p.embedding_model}")
    log("[text_pipeline] step=embed model=%s dim=%d chunks=%d questions=%d",
        p.embedding_model, p.embedding_dim, len(text_chunks), len(question_chunks))
    print(f"[text_pipeline] step=embed model={p.embedding_model} "
          f"dim={p.embedding_dim} chunks={len(text_chunks)} questions={len(question_chunks)}")
    connect_milvus()
    text_embedded: list[EmbeddedChunk] = embed_text(all_to_embed)
    log("[text_pipeline] step=embed embedded=%d", len(text_embedded))
    print(f"[text_pipeline] step=embed {len(text_embedded)} embeddings")
    if progress:
        progress.complete("embed", f"{len(text_embedded)} vectors")

    # Step 7 — index into Milvus
    if progress:
        progress.start("index", "indexing into Milvus")
    if text_embedded:
        log("[text_pipeline] step=index inserting %d vectors into Milvus collection=%s"
            " (%d doc chunks + %d question chunks)",
            len(text_embedded), cfg.text_collection, len(text_chunks), len(question_chunks))
        print(f"[text_pipeline] step=index collection={cfg.text_collection} "
              f"vectors={len(text_embedded)} "
              f"({len(text_chunks)} doc + {len(question_chunks)} question)")
        index_chunks(text_embedded)
    log("[text_pipeline] step=index done")
    print("[text_pipeline] step=index complete")
    if progress:
        progress.complete("index", f"{len(text_embedded)} vectors indexed")

    result = {
        "document_id": doc_id,
        "object_key": object_key,
        "chunks_created": len(text_chunks),
        "embeddings_indexed": len(text_embedded),
        "hyde_generated": hyde_count,
        "question_chunks_indexed": len(question_chunks),
    }
    log("[text_pipeline] done doc_id=%s chunks=%d embeddings=%d hyde=%d questions=%d",
        doc_id, len(text_chunks), len(text_embedded), hyde_count, len(question_chunks))
    print(f"[text_pipeline] done doc_id={doc_id} chunks={len(text_chunks)} "
          f"embeddings={len(text_embedded)} hyde={hyde_count} questions={len(question_chunks)}")
    return result
