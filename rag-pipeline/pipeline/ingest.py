"""File-based ingestion — directory/file scanning, duplicate detection, BM25 rebuild."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from tqdm import tqdm

from .bm25 import build_index as bm25_build
from .bm25 import load_index as bm25_load
from .chunk import chunk as chunk_doc
from .config import cfg
from .embed import embed_multimodal, embed_text
from .extract import extract
from .milvus import connect_milvus, index_chunks
from .models import Chunk, ChunkType, EmbeddedChunk, IngestionTier, RawDocument
from .ocr import ocr_pages
from .query import hypothetical_questions_for_chunk
from .tiers import IngestOptions, options_for_tier

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".rst", ".bmp", ".webp", ".gif"}


def _registry_path() -> Path:
    """Return the path to the ingestion registry file."""
    bm25_path = Path(cfg.bm25_index_path)
    return bm25_path.parent / "ingested_registry.json"


def _load_registry() -> dict:
    """Load the ingestion registry from disk."""
    path = _registry_path()
    if path.exists():
        try:
            with path.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_registry(registry: dict) -> None:
    """Persist the ingestion registry atomically."""
    path = _registry_path()
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    _, tmp = tempfile.mkstemp(prefix=".registry-", dir=str(directory))
    try:
        with Path(tmp).open("w") as f:
            json.dump(registry, f, indent=2)
            f.flush()
        Path(tmp).replace(path)
    except BaseException:
        with suppress(FileNotFoundError):
            Path(tmp).unlink()
        raise


def _file_md5(path: Path) -> str:
    """Compute the MD5 checksum of a file."""
    h = hashlib.md5()  # noqa: S324
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _ingest_file(file_path: Path, opts: IngestOptions) -> tuple[int, int, list[Chunk]]:  # noqa: C901, PLR0912
    """Ingest a single file: extract, chunk, embed, and index."""
    doc = extract(str(file_path), fast=opts.extract_pdf_text_directly)
    page_images = doc.images
    image_chunks_for_mm: list[Chunk] = []

    if page_images and not opts.extract_pdf_text_directly:
        ocr_texts = ocr_pages(page_images)
        ocr_text = "\n\n".join(t for t in ocr_texts if t)
        doc = RawDocument(path=doc.path, content_type=doc.content_type, text=ocr_text or doc.text, images=[], metadata=doc.metadata)
        if opts.use_multimodal_embedding:
            for i, img in enumerate(page_images):
                if not img:
                    continue
                image_chunks_for_mm.append(
                    Chunk(
                        id=uuid.uuid4().hex,
                        source_path=str(file_path),
                        text=ocr_texts[i] if i < len(ocr_texts) else "",
                        chunk_type=ChunkType.IMAGE,
                        metadata={**doc.metadata, "image_index": i, "ingestion_tier": opts.tier.value},
                        image_data=img,
                    )
                )
    else:
        has_text = bool(doc.text.strip())
        if not has_text and page_images:
            for i, img in enumerate(page_images):
                if not img:
                    continue
                image_chunks_for_mm.append(
                    Chunk(
                        id=uuid.uuid4().hex,
                        source_path=str(file_path),
                        text="",
                        chunk_type=ChunkType.IMAGE,
                        metadata={**doc.metadata, "image_index": i, "ingestion_tier": opts.tier.value},
                        image_data=img,
                    )
                )
        doc = RawDocument(path=doc.path, content_type=doc.content_type, text=doc.text, images=[], metadata=doc.metadata)

    text_chunks = chunk_doc(doc, opts.chunk_strategy or cfg.chunk_strategy)
    for c in text_chunks:
        c.metadata["ingestion_tier"] = opts.tier.value

    extra_chunks: list[Chunk] = []
    if opts.hypothetical_questions_per_chunk > 0:

        def _gen(c: Chunk) -> list[Chunk]:
            if not c.text.strip():
                return []
            return [
                Chunk(
                    id=uuid.uuid4().hex,
                    source_path=c.source_path,
                    text=q,
                    chunk_type=ChunkType.HYPOTHETICAL_QUESTION,
                    metadata={**c.metadata, "is_hypothetical_question": True},
                    parent_id=c.id,
                )
                for q in hypothetical_questions_for_chunk(c.text, n=opts.hypothetical_questions_per_chunk)
                if q.strip()
            ]

        with ThreadPoolExecutor(max_workers=8) as ex:
            for future in as_completed([ex.submit(_gen, c) for c in text_chunks]):
                extra_chunks.extend(future.result())

    all_text = text_chunks + extra_chunks
    embedded: list[EmbeddedChunk] = []
    if opts.use_text_embedding:
        embedded += embed_text(all_text)
    if image_chunks_for_mm:
        embedded += embed_multimodal(image_chunks_for_mm)
    index_chunks(embedded)

    all_chunks = all_text + image_chunks_for_mm
    bm25_chunks = [c for c in all_text if c.image_data is None]
    return len(all_chunks), len(embedded), bm25_chunks


def ingest_tier(
    path: str,
    tier: IngestionTier = IngestionTier.SLOW,
    *,
    skip_duplicates: bool = True,
    strategy: str | None = None,
    hypothetical_questions: bool | None = None,
) -> dict:
    """Ingest all supported files at a given path with the specified tier options."""
    connect_milvus()
    opts = options_for_tier(tier)
    if strategy is not None:
        valid_strategies = {"recursive", "sentence_window", "hierarchical"}
        if strategy not in valid_strategies:
            msg = "Unknown chunk strategy %r. Valid: recursive, sentence_window, hierarchical"
            raise ValueError(msg % strategy)
        opts = replace(opts, chunk_strategy=strategy)
    if hypothetical_questions is not None:
        q = max(opts.hypothetical_questions_per_chunk, cfg.hypothetical_questions_per_chunk) if hypothetical_questions else 0
        opts = replace(opts, hypothetical_questions_per_chunk=q)

    root = Path(path)
    file_paths = sorted(p for p in (root.rglob("*") if root.is_dir() else [root]) if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS)

    registry = _load_registry()
    processed = skipped = 0
    total_chunks = total_embeds = 0
    bm25_accum: list[Chunk] = []
    errors: list[dict[str, str]] = []

    for fp in tqdm(file_paths, desc=f"Ingesting [{tier.value}]", unit="file"):
        try:
            fhash = _file_md5(fp)
        except OSError:
            fhash = ""
        rkey = str(fp.resolve())
        if skip_duplicates and rkey in registry and registry[rkey].get("hash") == fhash and registry[rkey].get("tier") == tier.value:
            skipped += 1
            continue
        try:
            n, e, bm25_c = _ingest_file(fp, opts)
            processed += 1
            total_chunks += n
            total_embeds += e
            bm25_accum.extend(bm25_c)
            registry[rkey] = {"hash": fhash, "tier": tier.value, "chunks": n, "strategy": opts.chunk_strategy or cfg.chunk_strategy}
            _save_registry(registry)
        except Exception as exc:  # noqa: BLE001
            logger.info("[ingest] ERROR %s: %s", fp, exc)
            errors.append({"file": str(fp), "error": str(exc)})

    if bm25_accum:
        try:
            _, existing = bm25_load()
            bm25_accum = existing + bm25_accum
        except FileNotFoundError:
            pass
        bm25_build(bm25_accum)

    return {
        "tier": tier.value,
        "files_processed": processed,
        "files_failed": len(errors),
        "files_skipped_duplicate": skipped,
        "chunks_created": total_chunks,
        "embeddings_indexed": total_embeds,
        "errors": errors,
    }


def ingest_path(path: str, strategy: str | None = None, *, add_hypothetical_questions: bool = False) -> dict:
    """Ingest a path with SLOW tier and sensible defaults."""
    return ingest_tier(path, tier=IngestionTier.SLOW, skip_duplicates=True, strategy=strategy, hypothetical_questions=add_hypothetical_questions)
