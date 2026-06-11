"""Ingestion orchestrator for the RAG pipeline.

Ties together:
  extract → chunk → (optional hypothetical-question augmentation) → embed → index

The :func:`ingest_path` function is the main entry point.  It accepts a file
or directory path, processes every supported document it finds, keeps the
Milvus text collection and BM25 index up-to-date, and returns a stats dict.
"""

import hashlib
import json
import os
import uuid
from pathlib import Path

from tqdm import tqdm

from .config import cfg
from .models import RawDocument, Chunk, ChunkType, EmbeddedChunk
from .extract import extract
from .chunk import chunk as chunk_doc
from .embed import embed_all
from .index import (
    connect_milvus,
    index_chunks,
    build_bm25_index,
    save_bm25_index,
    load_bm25_index,
)
from .query import hypothetical_questions_for_chunk


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _registry_path() -> str:
    from .config import cfg
    return os.path.join(
        os.path.dirname(os.path.abspath(cfg.bm25_index_path)),
        "ingested_registry.json",
    )


def _load_registry() -> dict:
    path = _registry_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_registry(registry: dict) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(registry, f, indent=2)


# File extensions that ingest_path will recurse into when given a directory.
_SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg",
    ".rst", ".bmp", ".webp", ".gif",
}


def ingest_path(
    path: str,
    strategy: str | None = None,
    add_hypothetical_questions: bool = False,
) -> dict:
    """Ingest all documents from *path* (file or directory).

    Steps
    -----
    1. Connect to Milvus.
    2. Collect all file paths to process.  If *path* is a directory every
       file whose extension is in :data:`_SUPPORTED_EXTENSIONS` is included
       (recursively).  If *path* is a single file it is processed directly.
    3. For each file:
       a. :func:`~pipeline.extract.extract` → :class:`~pipeline.models.RawDocument`
       b. :func:`~pipeline.chunk.chunk` → ``list[Chunk]``
       c. If *add_hypothetical_questions* is ``True``, generate hypothetical
          questions for every **text** chunk (chunks where ``image_data`` is
          ``None``) and create a new ``ChunkType.TEXT`` chunk per question
          whose ``parent_id`` is the originating chunk's id.
       d. :func:`~pipeline.embed.embed_all` → ``list[EmbeddedChunk]``
       e. :func:`~pipeline.index.index_chunks` → persisted to Milvus.
       f. Collect all text chunks for the BM25 index.
    4. Attempt to load and extend any existing BM25 index; fall back to
       building a fresh index from this run's chunks alone.
    5. Save the BM25 index.
    6. Return a stats dict:
       ``{"files_processed": n, "chunks_created": n, "embeddings_indexed": n}``

    Errors for individual files are caught, printed, and skipped so that one
    bad file does not abort the entire run.

    Args:
        path:                       File or directory path to ingest.
        strategy:                   Chunking strategy override
                                    (``"recursive"``, ``"sentence_window"``,
                                    ``"hierarchical"``).  ``None`` uses
                                    ``cfg.chunk_strategy``.
        add_hypothetical_questions: When ``True``, augment each text chunk
                                    with LLM-generated hypothetical questions
                                    at index time.

    Returns:
        Stats dictionary with keys ``files_processed``, ``chunks_created``,
        and ``embeddings_indexed``.
    """
    connect_milvus()

    root = Path(path)
    if root.is_dir():
        file_paths = sorted(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
        )
    else:
        file_paths = [root]

    registry = _load_registry()
    files_processed = 0
    files_skipped = 0
    total_chunks = 0
    total_embeddings = 0
    all_text_chunks_for_bm25: list[Chunk] = []

    for file_path in tqdm(file_paths, desc="Ingesting files", unit="file"):
        # Skip files that have already been ingested with the same content.
        try:
            file_hash = _file_md5(file_path)
        except OSError:
            file_hash = ""

        registry_key = file_path.name
        if registry_key in registry and registry[registry_key].get("hash") == file_hash:
            files_skipped += 1
            continue

        try:
            # --- Extract ---
            doc: RawDocument = extract(str(file_path))

            # --- Chunk ---
            chunks: list[Chunk] = chunk_doc(doc, strategy)

            # --- Optional hypothetical-question augmentation ---
            extra_chunks: list[Chunk] = []
            if add_hypothetical_questions:
                text_chunks = [c for c in chunks if c.image_data is None]
                for chunk in text_chunks:
                    if not chunk.text.strip():
                        continue
                    questions = hypothetical_questions_for_chunk(chunk.text)
                    for question in questions:
                        if not question.strip():
                            continue
                        extra_chunks.append(
                            Chunk(
                                id=uuid.uuid4().hex,
                                source_path=chunk.source_path,
                                text=question,
                                chunk_type=ChunkType.TEXT,
                                metadata={**chunk.metadata, "is_hypothetical_question": True},
                                parent_id=chunk.id,
                            )
                        )

            all_chunks = chunks + extra_chunks

            # --- Embed ---
            embedded: list[EmbeddedChunk] = embed_all(all_chunks)

            # --- Index into Milvus ---
            index_chunks(embedded)

            # --- Collect text chunks for BM25 ---
            text_for_bm25 = [c for c in all_chunks if c.image_data is None]
            all_text_chunks_for_bm25.extend(text_for_bm25)

            files_processed += 1
            total_chunks += len(all_chunks)
            total_embeddings += len(embedded)

            # Record in registry so this file is skipped on future runs.
            registry[registry_key] = {
                "hash": file_hash,
                "chunks": len(all_chunks),
                "strategy": strategy or cfg.chunk_strategy,
            }
            _save_registry(registry)

        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] ERROR processing {file_path}: {exc}")
            continue

    # --- Build / extend BM25 index ---
    if all_text_chunks_for_bm25:
        try:
            existing_bm25, existing_chunks = load_bm25_index()
            combined_chunks = existing_chunks + all_text_chunks_for_bm25
        except FileNotFoundError:
            combined_chunks = all_text_chunks_for_bm25

        build_bm25_index(combined_chunks)

    return {
        "files_processed": files_processed,
        "files_skipped_duplicate": files_skipped,
        "chunks_created": total_chunks,
        "embeddings_indexed": total_embeddings,
        "embedding_info": {
            "model": cfg.text_embedding_model,
            "dimension": cfg.text_embedding_dim,
            "collection": cfg.text_collection,
        },
    }
