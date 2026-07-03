"""Document-level ingestion — used by the API job worker."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from .progress import ProgressTracker

logger = logging.getLogger(__name__)


class _Tracker:
    """Wraps an optional ProgressTracker so callers never need None-guards."""

    def __init__(self, tracker: ProgressTracker | None = None) -> None:
        self._t = tracker

    @property
    def inner(self) -> ProgressTracker | None:
        return self._t

    def start(self, name: str, detail: str = "") -> None:
        if self._t:
            self._t.start(name, detail)

    def complete(self, name: str, detail: str = "") -> None:
        if self._t:
            self._t.complete(name, detail)


def _store_doc(doc: RawDocument, existing_key: str, tracker: _Tracker, timing: dict) -> str:
    tracker.start("store_raw")
    t0 = time.time()
    key = existing_key or store.store_file(doc.path)
    timing["store_raw"] = round(time.time() - t0, 4)
    tracker.complete("store_raw", f"key={key[:16]}...")
    return key


def _upsert_document(  # noqa: PLR0913
    doc: RawDocument,
    doc_id: str | None,
    object_key: str,
    source_name: str,
    tracker: _Tracker,
    timing: dict,
) -> str:
    tracker.start("db_insert")
    t0 = time.time()
    pgdb.connect()
    meta = {"source_path": doc.path, "object_key": object_key, **doc.metadata}
    if doc_id:
        pgdb.update_document(doc_id=doc_id, extracted_text=doc.text, status="ready", metadata=meta)
        result = doc_id
    else:
        result = pgdb.insert_document(
            title=source_name, mime_type=doc.content_type.value,
            original_filename=doc.metadata.get("filename", ""),
            extracted_text=doc.text, metadata=meta,
        )
    timing["db_insert"] = round(time.time() - t0, 4)
    tracker.complete("db_insert", f"document_id={result}")
    return result


def _do_chunk(doc: RawDocument, strategy: str, tracker: _Tracker, timing: dict) -> list[Chunk]:
    tracker.start("chunk", f"strategy={strategy}")
    t0 = time.time()
    chunks = chunk_doc(doc, strategy)
    text_chunks = [c for c in chunks if c.image_data is None]
    timing["chunk"] = round(time.time() - t0, 4)
    tracker.complete("chunk", f"chunks={len(text_chunks)}")
    return text_chunks


def _do_summary(
    doc: RawDocument, doc_id: str, *, gen_summary: bool,
    tracker: _Tracker, timing: dict,
) -> Chunk | None:
    if not gen_summary or not doc.text.strip():
        return None
    tracker.start("summary")
    t0 = time.time()
    text = _chat(
        "You are an expert at summarising documents. Given a document, produce a concise summary (3-5 sentences) "
        "that captures the key topics, themes, and findings. Output only the summary text, nothing else.",
        f"Document:\n{doc.text[:8000]}",
    ).strip()
    chunk = None
    if text:
        chunk = Chunk(id=uuid.uuid4().hex, source_path=doc.path, text=text,
                       chunk_type=ChunkType.SUMMARY, metadata=dict(doc.metadata))
        pgdb.execute("UPDATE documents SET analysis_summary = %s, updated_at = NOW() WHERE id = %s", (text, doc_id))
    timing["summary"] = round(time.time() - t0, 4)
    tracker.complete("summary", f"generated={'yes' if chunk else 'no'}")
    return chunk


def _build_hyde_rows(  # noqa: PLR0913
    text_chunks: list[Chunk], *,
    hyde: bool, hyde_n: int, doc_id: str, chunk_offset: int, strategy: str,
    tracker: _Tracker, timing: dict,
) -> tuple[list[Chunk], list[dict], int]:
    tracker.start("hyde")
    t0 = time.time()
    hyde_count = 0
    question_chunks: list[Chunk] = []
    chunk_rows: list[dict] = []
    embed_params = {
        "embedding_model": cfg.text_embedding_model, "embedding_dim": cfg.text_embedding_dim,
        "chunk_strategy": strategy, "chunk_size": cfg.chunk_size, "chunk_overlap": cfg.chunk_overlap,
    }
    for idx, c in enumerate(text_chunks):
        meta = dict(c.metadata)
        meta["embedding_params"] = embed_params
        meta["chunk_type"] = c.chunk_type.value if hasattr(c.chunk_type, "value") else str(c.chunk_type)
        questions = hypothetical_questions_for_chunk(c.text, n=hyde_n) if hyde and hyde_n > 0 and c.text.strip() else []
        hyde_count += len(questions)
        question_chunks.extend(
            Chunk(
                id=uuid.uuid4().hex, source_path=c.source_path, text=q_text,
                chunk_type=ChunkType.HYPOTHETICAL_QUESTION, parent_id=c.id,
                metadata={"chunk_type": ChunkType.HYPOTHETICAL_QUESTION.value,
                          "source_chunk_id": c.id, "chunk_index": idx},
            )
            for q_text in questions if q_text.strip()
        )
        meta["hyde_questions"] = questions
        meta["parent_id"] = c.parent_id or ""
        if c.window_text:
            meta["window_text"] = c.window_text
        chunk_rows.append({"chunk_index": chunk_offset + idx, "content": c.text, "metadata": meta, "document_id": doc_id})
    timing["hyde"] = round(time.time() - t0, 4)
    tracker.complete("hyde", f"questions={hyde_count}")
    return question_chunks, chunk_rows, hyde_count


def _persist_embed_index(
    chunk_rows: list[dict], all_embed: list[Chunk],
    tracker: _Tracker, timing: dict,
) -> list[Chunk]:
    t0 = time.time()
    tracker.start("persist", f"rows={len(chunk_rows)}")
    if chunk_rows:
        pgdb.insert_chunks_batch(chunk_rows)
    tracker.complete("persist", f"chunks={len(chunk_rows)}")
    timing["persist"] = round(time.time() - t0, 4)

    t0 = time.time()
    tracker.start("embed", f"items={len(all_embed)}")
    connect_milvus()
    embedded = embed_text(all_embed)
    tracker.complete("embed", f"vectors={len(embedded)}")
    timing["embed"] = round(time.time() - t0, 4)

    t0 = time.time()
    tracker.start("index", f"vectors={len(embedded)}")
    if embedded:
        index_chunks(embedded)
    tracker.complete("index", f"collection={cfg.text_collection}")
    timing["index"] = round(time.time() - t0, 4)
    return embedded


def process_text(doc: RawDocument, params: dict | None = None) -> dict:
    """Process a text document: chunk, embed, index."""
    p = params or {}
    strategy = p.get("chunk_strategy", cfg.chunk_strategy)
    hyde = p.get("generate_hyde", False)
    hyde_n = int(p.get("hyde_per_chunk", cfg.hypothetical_questions_per_chunk))
    gen_summary = p.get("generate_summary", True)
    existing_doc_id = p.get("existing_document_id")
    existing_object_key = p.get("existing_object_key", "")
    chunk_offset = int(p.get("chunk_index_offset", 0))
    tracker = _Tracker(p.get("progress_tracker"))
    source_name = doc.metadata.get("filename", doc.path)
    timing: dict[str, float] = {}

    object_key = _store_doc(doc, existing_object_key, tracker, timing)
    doc_id = _upsert_document(doc, existing_doc_id, object_key, source_name, tracker, timing)
    text_chunks = _do_chunk(doc, strategy, tracker, timing)
    summary_chunk = _do_summary(doc, doc_id, gen_summary=gen_summary, tracker=tracker, timing=timing)
    question_chunks, chunk_rows, hyde_count = _build_hyde_rows(
        text_chunks, hyde=hyde, hyde_n=hyde_n, doc_id=doc_id, chunk_offset=chunk_offset,
        strategy=strategy, tracker=tracker, timing=timing,
    )

    all_embed = text_chunks + question_chunks
    if summary_chunk:
        all_embed.append(summary_chunk)
    embedded = _persist_embed_index(chunk_rows, all_embed, tracker, timing)

    timing["total"] = round(sum(v for v in timing.values()), 4)
    return {
        "document_id": doc_id, "object_key": object_key,
        "chunks_created": len(text_chunks), "embeddings_indexed": len(embedded),
        "hyde_generated": hyde_count, "question_chunks_indexed": len(question_chunks),
        "summary_indexed": 1 if summary_chunk else 0,
        "summary_text": summary_chunk.text if summary_chunk else "",
        "embedding_info": {"model": cfg.text_embedding_model, "dimension": cfg.text_embedding_dim,
                           "collection": cfg.text_collection, "chunk_strategy": strategy},
        "timing": timing,
    }


def _image_store_ocr(
    doc: RawDocument, ocr_mode: str, max_h: int, tracker: _Tracker,
) -> tuple:
    tracker.start("store_raw")
    object_key = store.store_file(doc.path)
    tracker.complete("store_raw", f"key={object_key[:16]}...")

    tracker.start("ocr", f"mode={ocr_mode}")
    prepared = preprocess_images(doc.images, max_h)
    derived_keys = [store.store(item.data, suffix=".png") for item in prepared]
    ocr_results = ocr_pages_detailed([item.data for item in prepared], mode=ocr_mode)
    ocr_texts = [r.text for r in ocr_results]
    combined = "\n\n".join(t for t in (doc.text.strip(), "\n\n".join(t for t in ocr_texts if t.strip())) if t)
    tracker.complete("ocr", f"chars={len(combined)}; images={len(prepared)}")
    return object_key, prepared, derived_keys, ocr_results, combined


def _image_upsert(  # noqa: PLR0913
    doc: RawDocument, doc_id: str | None, combined: str, ocr_mode: str,
    object_key: str, prepared: list, source_name: str, tracker: _Tracker,
) -> str:
    tracker.start("db_insert")
    pgdb.connect()
    meta = {"source_path": doc.path, "object_key": object_key,
            "source_image_count": len(doc.images), "derived_image_count": len(prepared), **doc.metadata}
    if doc_id:
        pgdb.update_document(doc_id=doc_id, extracted_text=combined, status="ready", ocr_mode=ocr_mode, metadata=meta)
        result = doc_id
    else:
        result = pgdb.insert_document(title=source_name, mime_type=doc.content_type.value,
            original_filename=source_name, sha256=object_key, extracted_text=combined,
            ocr_mode=ocr_mode, metadata=meta)
    tracker.complete("db_insert", f"document_id={result}")
    return result


def _build_image_chunks(  # noqa: PLR0913
    prepared: list, derived_keys: list, ocr_results: list, object_key: str,
    doc: RawDocument, doc_id: str, ocr_mode: str,
) -> tuple[list[Chunk], list[dict]]:
    image_chunks: list[Chunk] = []
    chunk_rows: list[dict] = []
    for idx, (item, key, ocr_result) in enumerate(zip(prepared, derived_keys, ocr_results, strict=True)):
        asset_id = pgdb.insert_asset(
            document_id=doc_id,
            asset_type="page" if doc.content_type.value == "pdf" else "image",
            mime_type="image/png",
            page_number=item.source_index + 1, text=ocr_result.text,
            source_index=item.source_index, derived_index=item.derived_index,
            sha256=hashlib.sha256(item.data).hexdigest(),
            width=item.width, height=item.height,
            ocr_backend=ocr_mode, ocr_status=ocr_result.status,
            preprocessing={"operations": list(item.operations)},
            metadata={"raw_object_key": object_key, "ocr_error": ocr_result.error},
        )
        chunk = Chunk(id=uuid.uuid4().hex, source_path=doc.path, text=ocr_result.text,
                      chunk_type=ChunkType.IMAGE,
                      metadata={**doc.metadata, "document_id": doc_id, "asset_id": asset_id,
                                "source_index": item.source_index, "derived_index": item.derived_index,
                                "object_key": key, "ocr_mode": ocr_mode},
                      image_data=item.data)
        image_chunks.append(chunk)
        chunk_rows.append({"document_id": doc_id, "asset_id": asset_id, "chunk_index": idx,
                           "content": ocr_result.text,
                           "metadata": {**chunk.metadata, "chunk_type": ChunkType.IMAGE.value}})
    return image_chunks, chunk_rows


def _image_embed_index(image_chunks: list[Chunk], *, use_mm: bool, tracker: _Tracker) -> list[Chunk]:
    tracker.start("embed", f"image_chunks={len(image_chunks)}")
    embedded: list[Chunk] = []
    if use_mm and image_chunks:
        connect_milvus()
        try:
            embedded = embed_multimodal(image_chunks)
        except BaseException:
            logger.exception("Multimodal embedding failed; continuing without image vectors")
    tracker.complete("embed", f"image_vectors={len(embedded)}")

    tracker.start("index", f"image_vectors={len(embedded)}")
    if embedded:
        index_chunks(embedded)
    tracker.complete("index", f"image_vectors={len(embedded)}; collection={cfg.image_collection}")
    return embedded


def _image_text_track(  # noqa: PLR0913
    doc: RawDocument, combined: str, ocr_mode: str, doc_id: str,
    object_key: str, chunk_rows: list, strategy: str, *,
    hyde: bool, use_text: bool, tracker: _Tracker,
) -> dict | None:
    if not use_text or not combined.strip():
        return None
    text_doc = RawDocument(path=doc.path, content_type=doc.content_type, text=combined, images=[],
                           metadata={**doc.metadata, "ocr_mode": ocr_mode, "image_document_id": doc_id})
    return process_text(text_doc, {"existing_document_id": doc_id, "existing_object_key": object_key,
                                   "chunk_index_offset": len(chunk_rows), "chunk_strategy": strategy,
                                   "generate_hyde": hyde, "generate_summary": False,
                                   "progress_tracker": tracker.inner})


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
    tracker = _Tracker(p.get("progress_tracker"))
    source_name = doc.metadata.get("filename", doc.path)

    object_key, prepared, derived_keys, ocr_results, combined = _image_store_ocr(doc, ocr_mode, max_h, tracker)
    doc_id = _image_upsert(doc, existing_doc_id, combined, ocr_mode, object_key, prepared, source_name, tracker)
    image_chunks, chunk_rows = _build_image_chunks(prepared, derived_keys, ocr_results, object_key, doc, doc_id, ocr_mode)
    _persist_image_chunks(chunk_rows, tracker)
    embedded = _image_embed_index(image_chunks, use_mm=use_mm, tracker=tracker)
    text_result = _image_text_track(
        doc, combined, ocr_mode, doc_id, object_key, chunk_rows, strategy,
        hyde=hyde, use_text=use_text, tracker=tracker,
    )
    return {
        "document_id": doc_id, "object_key": object_key,
        "source_images": len(doc.images), "derived_images": len(prepared),
        "chunks_created": len(image_chunks) + (text_result or {}).get("chunks_created", 0),
        "image_embeddings_indexed": len(embedded),
        "text_embeddings_indexed": (text_result or {}).get("embeddings_indexed", 0),
        "embeddings_indexed": len(embedded) + (text_result or {}).get("embeddings_indexed", 0),
        "ocr_mode": ocr_mode, "ocr_characters": len(combined),
        "embedding_info": {"model": p.get("embedding_model", cfg.multimodal_embedding_model),
                           "dimension": p.get("embedding_dim", cfg.multimodal_embedding_dim),
                           "collection": p.get("milvus_collection", cfg.image_collection)},
    }


def _persist_image_chunks(chunk_rows: list[dict], tracker: _Tracker) -> None:
    tracker.start("persist", f"image_chunks={len(chunk_rows)}")
    if chunk_rows:
        pgdb.insert_chunks_batch(chunk_rows)
    tracker.complete("persist", f"image_chunks={len(chunk_rows)}")
