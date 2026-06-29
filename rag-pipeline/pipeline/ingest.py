"""Ingestion orchestrator for the RAG pipeline.

For the three-tier API see :mod:`pipeline.tiers`.  This module keeps the
original :func:`ingest_path` entry point for backwards compatibility and
exposes it as a thin wrapper over the tier system (defaulting to SLOW).

Two-track ingestion for PDF documents (slow tier):
  Track A (text):  page images → PaddleOCR-VL (OCR) → text chunks
                   → text embedding → Milvus text collection
  Track B (image): page images → multimodal embedding (/pooling, image-only)
                   → Milvus image collection

Instant tier skips both OCR and Track B, using PyMuPDF text extraction only.

The :func:`ingest_path` function is the main entry point for the legacy API.
"""

import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tqdm import tqdm

from .config import cfg
from .models import IngestionTier, RawDocument, Chunk, ChunkType, EmbeddedChunk
from .extract import extract, extract_fast
from .ocr import ocr_pages
from .chunk import chunk as chunk_doc
from .embed import embed_text, embed_multimodal
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
    """Ingest all documents from *path* (file or directory) using the SLOW tier.

    This function is kept for backwards compatibility.  New callers should use
    :func:`pipeline.tiers.ingest_tier` for explicit tier control.

    Steps
    -----
    1. Connect to Milvus.
    2. Collect file paths to process (recursive for directories).
    3. For each file:
       a. :func:`~pipeline.extract.extract` → :class:`~pipeline.models.RawDocument`
       b. OCR page images concurrently (PDFs only).
       c. :func:`~pipeline.chunk.chunk` → ``list[Chunk]``
       d. If *add_hypothetical_questions* is ``True``, generate questions
          per text chunk and index them as separate chunks.
       e. :func:`~pipeline.embed.embed_text` + :func:`~pipeline.embed.embed_multimodal`
       f. :func:`~pipeline.index.index_chunks` → Milvus.
    4. Build / extend BM25 index.
    5. Return stats dict.

    Args:
        path:                       File or directory path to ingest.
        strategy:                   Chunking strategy override
                                    (``"recursive"``, ``"sentence_window"``,
                                    ``"hierarchical"``).  ``None`` uses
                                    ``cfg.chunk_strategy``.
        add_hypothetical_questions: Augment each text chunk with LLM-generated
                                    hypothetical questions at index time.

    Returns:
        Stats dict: ``files_processed``, ``files_skipped_duplicate``,
        ``chunks_created``, ``embeddings_indexed``.
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
        try:
            file_hash = _file_md5(file_path)
        except OSError:
            file_hash = ""

        # Use resolved absolute path as registry key to avoid filename collisions
        registry_key = str(file_path.resolve())
        if registry_key in registry and registry[registry_key].get("hash") == file_hash:
            files_skipped += 1
            continue

        try:
            # --- Extract ---
            doc: RawDocument = extract(str(file_path))

            page_images = list(doc.images) if doc.images else []
            ocr_texts: list[str] = []
            image_chunks_placeholder: list[Chunk] = []

            if page_images:
                # Track B placeholder chunks (filled after OCR)
                for i, img_bytes in enumerate(doc.images):
                    image_chunks_placeholder.append(Chunk(
                        id=uuid.uuid4().hex,
                        source_path=doc.path,
                        text="",
                        chunk_type=ChunkType.IMAGE,
                        metadata={**doc.metadata, "image_index": i,
                                   "ingestion_tier": IngestionTier.SLOW.value},
                        image_data=img_bytes,
                    ))

                # OCR runs concurrently in a background thread
                with ThreadPoolExecutor(max_workers=1) as ocr_executor:
                    ocr_future = ocr_executor.submit(ocr_pages, page_images)
                    ocr_texts = ocr_future.result()

                # Backfill OCR text
                doc_text = "\n\n".join(t for t in ocr_texts if t)
                for i, chunk in enumerate(image_chunks_placeholder):
                    if i < len(ocr_texts):
                        chunk.text = ocr_texts[i]
            else:
                doc_text = doc.text

            # Rebuild doc with full OCR text
            doc = RawDocument(
                path=doc.path,
                content_type=doc.content_type,
                text=doc_text,
                images=[],
                metadata=doc.metadata,
            )

            # --- Track A: chunk + embed text ---
            text_chunks: list[Chunk] = chunk_doc(doc, strategy)
            for c in text_chunks:
                c.metadata["ingestion_tier"] = IngestionTier.SLOW.value

            # --- Optional hypothetical-question augmentation ---
            extra_chunks: list[Chunk] = []
            if add_hypothetical_questions:
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
                                chunk_type=ChunkType.HYPOTHETICAL_QUESTION,
                                metadata={**chunk.metadata,
                                           "is_hypothetical_question": True},
                                parent_id=chunk.id,
                            )
                        )

            all_text_chunks = text_chunks + extra_chunks

            # --- Embed both tracks ---
            embedded_text   = embed_text(all_text_chunks)
            embedded_images = embed_multimodal(image_chunks_placeholder)
            embedded: list[EmbeddedChunk] = embedded_text + embedded_images

            # --- Index into Milvus ---
            index_chunks(embedded)

            # --- Collect text chunks for BM25 ---
            text_for_bm25 = [c for c in all_text_chunks if c.image_data is None]
            all_text_chunks_for_bm25.extend(text_for_bm25)

            all_chunks = all_text_chunks + image_chunks_placeholder
            files_processed += 1
            total_chunks    += len(all_chunks)
            total_embeddings += len(embedded)

            registry[registry_key] = {
                "hash":     file_hash,
                "tier":     IngestionTier.SLOW.value,
                "chunks":   len(all_chunks),
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
        "files_processed":       files_processed,
        "files_skipped_duplicate": files_skipped,
        "chunks_created": total_chunks,
        "embeddings_indexed": total_embeddings,
        "embedding_info": {
            "model": cfg.text_embedding_model,
            "dimension": cfg.text_embedding_dim,
            "collection": cfg.text_collection,
        },
    }
