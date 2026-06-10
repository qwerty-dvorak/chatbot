"""Three-tier ingestion system for the RAG pipeline.

Tiers define the processing depth applied to a document at ingest time and
the search quality used when retrieving against it.

+---------+----------------------------------------------------------+
| Tier    | Use case                                                 |
+=========+==========================================================+
| instant | Chat-session file uploads.  Target < 2 s per page.      |
|         | Text-only, no OCR, no multimodal, no query enhancement. |
+---------+----------------------------------------------------------+
| slow    | Single-file deep processing.  Both text and multimodal  |
|         | embedding.  Full OCR.  HyDE at search time.             |
+---------+----------------------------------------------------------+
| global  | Batch-mode global knowledge base.  Hierarchical chunks, |
|         | hypothetical-question augmentation, all query           |
|         | enhancements using the chatbot-service LLM.             |
+---------+----------------------------------------------------------+

Tier promotion
--------------
A document ingested at instant can later be promoted to slow or global as a
background process.  ``promote_document`` removes the old chunks from Milvus
and re-ingests the file at the target tier.

Usage
-----
::

    from pipeline.tiers import ingest_tier, promote_document, IngestionTier

    # Ingest a chat-upload immediately
    ingest_tier("/tmp/upload.pdf", tier=IngestionTier.INSTANT)

    # Later, promote it to the global index in the background
    promote_document("/tmp/upload.pdf", to_tier=IngestionTier.GLOBAL)
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from tqdm import tqdm

from .config import cfg
from .models import IngestionTier, Chunk, ChunkType, EmbeddedChunk, RawDocument
from .extract import extract, extract_fast
from .ocr import ocr_pages
from .chunk import chunk as chunk_doc
from .embed import embed_text, embed_multimodal
from .index import (
    connect_milvus,
    index_chunks,
    build_bm25_index,
    load_bm25_index,
    delete_chunks_by_source,
)
from .query import hypothetical_questions_for_chunk


# ---------------------------------------------------------------------------
# IngestOptions — per-tier configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IngestOptions:
    """All knobs that vary between ingestion tiers."""

    tier: IngestionTier

    # --- extraction ---
    # When True, use PyMuPDF text layer instead of render-then-OCR for PDFs.
    extract_pdf_text_directly: bool
    # DPI for PDF page rendering; ignored when extract_pdf_text_directly=True.
    ocr_dpi: int

    # --- embedding tracks ---
    use_text_embedding: bool
    use_multimodal_embedding: bool

    # --- chunking ---
    # Empty string → use cfg.chunk_strategy.
    chunk_strategy: str

    # --- index-time augmentation ---
    # 0 = disabled.
    hypothetical_questions_per_chunk: int

    # --- search behaviour (used by the API when searching at this tier) ---
    # List of enhancement names: "hyde", "sub_queries", "stepback".
    query_enhancements: tuple[str, ...]
    use_reranker: bool


# ---------------------------------------------------------------------------
# Pre-built tier options
# ---------------------------------------------------------------------------

INSTANT_OPTIONS = IngestOptions(
    tier=IngestionTier.INSTANT,
    # Use PyMuPDF text layer — no OCR, no image rendering.
    extract_pdf_text_directly=True,
    ocr_dpi=72,                       # unused when extract_pdf_text_directly=True
    use_text_embedding=True,
    use_multimodal_embedding=False,   # skip multimodal for speed
    chunk_strategy="recursive",
    hypothetical_questions_per_chunk=0,
    query_enhancements=(),            # no enhancements — raw query only
    use_reranker=False,               # skip reranker for speed
)

SLOW_OPTIONS = IngestOptions(
    tier=IngestionTier.SLOW,
    extract_pdf_text_directly=False,  # full OCR via PaddleOCR-VL
    ocr_dpi=150,
    use_text_embedding=True,
    use_multimodal_embedding=True,
    chunk_strategy="sentence_window",
    hypothetical_questions_per_chunk=2,
    query_enhancements=("hyde",),
    use_reranker=True,
)

GLOBAL_OPTIONS = IngestOptions(
    tier=IngestionTier.GLOBAL,
    extract_pdf_text_directly=False,
    ocr_dpi=200,                      # higher DPI for better OCR quality
    use_text_embedding=True,
    use_multimodal_embedding=True,
    chunk_strategy="hierarchical",
    hypothetical_questions_per_chunk=3,
    query_enhancements=("hyde", "sub_queries", "stepback"),
    use_reranker=True,
)

_TIER_MAP: dict[IngestionTier, IngestOptions] = {
    IngestionTier.INSTANT: INSTANT_OPTIONS,
    IngestionTier.SLOW:    SLOW_OPTIONS,
    IngestionTier.GLOBAL:  GLOBAL_OPTIONS,
}


def options_for_tier(tier: IngestionTier) -> IngestOptions:
    """Return the :class:`IngestOptions` for a given :class:`IngestionTier`."""
    return _TIER_MAP[tier]


def tier_from_str(s: str) -> IngestionTier:
    """Parse a tier string, raising ValueError for unknown values."""
    try:
        return IngestionTier(s.lower())
    except ValueError:
        valid = ", ".join(t.value for t in IngestionTier)
        raise ValueError(f"Unknown tier {s!r}. Valid values: {valid}")


# ---------------------------------------------------------------------------
# Registry helpers (duplicated from ingest.py but scoped to tiers)
# ---------------------------------------------------------------------------

_SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg",
    ".rst", ".bmp", ".webp", ".gif",
}


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
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".registry-", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(registry, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _registry_key(file_path: Path) -> str:
    """Stable registry key for a file — uses the resolved absolute path."""
    return str(file_path.resolve())


# ---------------------------------------------------------------------------
# Core ingestion with explicit options
# ---------------------------------------------------------------------------

def _ingest_file(file_path: Path, opts: IngestOptions) -> tuple[int, int, list[Chunk]]:
    """Ingest a single file according to *opts*.

    Returns (chunks_created, embeddings_indexed, bm25_text_chunks).
    bm25_text_chunks are the text-only chunks to append to the BM25 index.
    """
    # --- Extract ---
    doc: RawDocument = (
        extract_fast(str(file_path))
        if opts.extract_pdf_text_directly
        else extract(str(file_path))
    )

    page_images = doc.images
    image_chunks_for_mm: list[Chunk] = []

    if page_images and not opts.extract_pdf_text_directly:
        ocr_texts = ocr_pages(page_images)

        ocr_text = "\n\n".join(t for t in ocr_texts if t)
        doc_text = ocr_text or doc.text
        doc = RawDocument(
            path=doc.path,
            content_type=doc.content_type,
            text=doc_text,
            images=[],          # image chunks built separately below
            metadata=doc.metadata,
        )

        if opts.use_multimodal_embedding:
            for i, img_bytes in enumerate(page_images):
                if not img_bytes:
                    continue
                image_chunks_for_mm.append(Chunk(
                    id=uuid.uuid4().hex,
                    source_path=str(file_path),
                    text=ocr_texts[i] if i < len(ocr_texts) else "",
                    chunk_type=ChunkType.IMAGE,
                    metadata={**doc.metadata, "image_index": i,
                               "ingestion_tier": opts.tier.value},
                    image_data=img_bytes,
                ))
    else:
        # Text file or fast PDF: no images, doc.text already populated
        doc = RawDocument(
            path=doc.path,
            content_type=doc.content_type,
            text=doc.text,
            images=[],
            metadata=doc.metadata,
        )

    # --- Chunk text ---
    strategy = opts.chunk_strategy or cfg.chunk_strategy
    text_chunks: list[Chunk] = chunk_doc(doc, strategy)
    # Stamp tier into metadata
    for c in text_chunks:
        c.metadata["ingestion_tier"] = opts.tier.value

    # --- Hypothetical-question augmentation ---
    extra_chunks: list[Chunk] = []
    if opts.hypothetical_questions_per_chunk > 0:
        for chunk in text_chunks:
            if not chunk.text.strip():
                continue
            questions = hypothetical_questions_for_chunk(
                chunk.text, n=opts.hypothetical_questions_per_chunk
            )
            for q in questions:
                if not q.strip():
                    continue
                extra_chunks.append(Chunk(
                    id=uuid.uuid4().hex,
                    source_path=chunk.source_path,
                    text=q,
                    chunk_type=ChunkType.TEXT,
                    metadata={**chunk.metadata, "is_hypothetical_question": True},
                    parent_id=chunk.id,
                ))

    all_text_chunks = text_chunks + extra_chunks

    # --- Embed ---
    embedded: list[EmbeddedChunk] = []
    if opts.use_text_embedding:
        embedded += embed_text(all_text_chunks)
    if opts.use_multimodal_embedding and image_chunks_for_mm:
        embedded += embed_multimodal(image_chunks_for_mm)

    # --- Index ---
    index_chunks(embedded)

    all_chunks = all_text_chunks + image_chunks_for_mm
    bm25_text_chunks = [c for c in all_text_chunks if c.image_data is None]
    return len(all_chunks), len(embedded), bm25_text_chunks


def ingest_tier(
    path: str,
    tier: IngestionTier = IngestionTier.SLOW,
    skip_duplicates: bool = True,
    strategy: str | None = None,
    hypothetical_questions: bool | None = None,
) -> dict:
    """Ingest one file or directory at a given tier.

    Parameters
    ----------
    path:
        File or directory to ingest.
    tier:
        Processing depth.  One of ``IngestionTier.INSTANT``,
        ``SLOW``, or ``GLOBAL``.
    skip_duplicates:
        When True, skip files whose MD5 hash and tier match the registry.

    Returns
    -------
    dict with keys ``files_processed``, ``files_skipped_duplicate``,
    ``chunks_created``, ``embeddings_indexed``.
    """
    connect_milvus()
    opts = options_for_tier(tier)
    if strategy is not None:
        valid_strategies = {"recursive", "sentence_window", "hierarchical"}
        if strategy not in valid_strategies:
            raise ValueError(
                f"Unknown chunk strategy {strategy!r}. "
                f"Valid values: {', '.join(sorted(valid_strategies))}"
            )
        opts = replace(opts, chunk_strategy=strategy)
    if hypothetical_questions is not None:
        question_count = (
            max(opts.hypothetical_questions_per_chunk, cfg.hypothetical_questions_per_chunk)
            if hypothetical_questions
            else 0
        )
        opts = replace(opts, hypothetical_questions_per_chunk=question_count)

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
    errors: list[dict[str, str]] = []

    for file_path in tqdm(file_paths, desc=f"Ingesting [{tier.value}]", unit="file"):
        try:
            file_hash = _file_md5(file_path)
        except OSError:
            file_hash = ""

        rkey = _registry_key(file_path)
        if skip_duplicates and rkey in registry:
            stored = registry[rkey]
            if stored.get("hash") == file_hash and stored.get("tier") == tier.value:
                files_skipped += 1
                continue

        try:
            chunks_n, embeds_n, bm25_chunks = _ingest_file(file_path, opts)
            files_processed += 1
            total_chunks += chunks_n
            total_embeddings += embeds_n
            all_text_chunks_for_bm25.extend(bm25_chunks)

            registry[rkey] = {
                "hash": file_hash,
                "tier": tier.value,
                "chunks": chunks_n,
                "strategy": opts.chunk_strategy or cfg.chunk_strategy,
            }
            _save_registry(registry)

        except Exception as exc:  # noqa: BLE001
            print(f"[ingest_tier] ERROR processing {file_path}: {exc}")
            errors.append({"file": str(file_path), "error": str(exc)})
            continue

    # --- BM25 rebuild ---
    if all_text_chunks_for_bm25:
        try:
            existing_bm25, existing_chunks = load_bm25_index()
            combined = existing_chunks + all_text_chunks_for_bm25
        except FileNotFoundError:
            combined = all_text_chunks_for_bm25
        build_bm25_index(combined)

    return {
        "tier": tier.value,
        "files_processed": files_processed,
        "files_failed": len(errors),
        "files_skipped_duplicate": files_skipped,
        "chunks_created": total_chunks,
        "embeddings_indexed": total_embeddings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Tier promotion
# ---------------------------------------------------------------------------

def promote_document(
    source_path: str,
    to_tier: IngestionTier,
    delete_old_chunks: bool = True,
) -> dict:
    """Re-ingest *source_path* at *to_tier*, optionally removing old chunks.

    This is the primary mechanism for upgrading a document from a lower tier
    (e.g. instant chat upload) to a higher tier (slow or global) as a
    background process.

    Parameters
    ----------
    source_path:
        Absolute path to the original file.  Must still exist on disk.
    to_tier:
        Target :class:`IngestionTier`.
    delete_old_chunks:
        When True (default), delete existing Milvus vectors for this file
        before re-ingesting at the new tier so duplicates do not accumulate.

    Returns
    -------
    dict with ingestion stats plus ``deleted_text_chunks`` and
    ``deleted_image_chunks`` counts.
    """
    p = Path(source_path)
    if not p.exists():
        raise FileNotFoundError(f"Source file not found: {source_path!r}")

    connect_milvus()

    deleted_text = 0
    deleted_image = 0
    if delete_old_chunks:
        deleted_text  = delete_chunks_by_source(source_path, cfg.text_collection)
        deleted_image = delete_chunks_by_source(source_path, cfg.image_collection)
        try:
            _, chunks = load_bm25_index()
        except FileNotFoundError:
            chunks = []
        retained_chunks = [chunk for chunk in chunks if chunk.source_path != source_path]
        if retained_chunks:
            build_bm25_index(retained_chunks)
        else:
            try:
                os.unlink(cfg.bm25_index_path)
            except FileNotFoundError:
                pass

    # Remove registry entry so ingest_tier does not skip as duplicate
    registry = _load_registry()
    rkey = _registry_key(p)
    registry.pop(rkey, None)
    _save_registry(registry)

    stats = ingest_tier(str(p), tier=to_tier, skip_duplicates=False)
    stats["deleted_text_chunks"]  = deleted_text
    stats["deleted_image_chunks"] = deleted_image
    return stats
