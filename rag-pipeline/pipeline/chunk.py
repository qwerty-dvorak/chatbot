"""Chunking strategies for the RAG pipeline.

Three strategies are supported, selected by cfg.chunk_strategy or the
optional *strategy* argument to :func:`chunk`:

* ``recursive``        – parent/child two-level split
* ``sentence_window``  – per-sentence chunks with surrounding window text
* ``hierarchical``     – paragraph-grouped summary + fine-grained children
"""

import uuid
from typing import Optional

from .config import cfg
from .models import Chunk, ChunkType, RawDocument


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return uuid.uuid4().hex


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    """Split *text* into fixed-size chunks with *overlap* characters of overlap."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return [c for c in chunks if c.strip()]


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences on '. ', '? ', '! ' boundaries.

    The terminating punctuation is kept with the sentence that ends with it.
    """
    sentences: list[str] = []
    current: list[str] = []

    i = 0
    while i < len(text):
        current.append(text[i])
        # Check for sentence-ending patterns: '. ', '? ', '! '
        if text[i] in ".?!" and i + 1 < len(text) and text[i + 1] == " ":
            sentence = "".join(current).strip()
            if sentence:
                sentences.append(sentence)
            current = []
            i += 2  # skip the trailing space
            continue
        i += 1

    # Whatever remains (no trailing punctuation + space)
    remainder = "".join(current).strip()
    if remainder:
        sentences.append(remainder)

    return sentences


# ---------------------------------------------------------------------------
# Chunking strategies
# ---------------------------------------------------------------------------

def _chunk_recursive(doc: RawDocument) -> list[Chunk]:
    """Two-level parent → child chunking."""
    parent_texts = _split_text(doc.text, cfg.parent_chunk_size, cfg.chunk_overlap)
    all_chunks: list[Chunk] = []

    for parent_text in parent_texts:
        parent_id = _new_id()
        parent_chunk = Chunk(
            id=parent_id,
            source_path=doc.path,
            text=parent_text,
            chunk_type=ChunkType.PARENT,
            metadata={**doc.metadata},
        )

        child_texts = _split_text(parent_text, cfg.chunk_size, cfg.chunk_overlap)
        child_ids: list[str] = []

        for child_text in child_texts:
            child_id = _new_id()
            child_chunk = Chunk(
                id=child_id,
                source_path=doc.path,
                text=child_text,
                chunk_type=ChunkType.CHILD,
                metadata={**doc.metadata},
                parent_id=parent_id,
            )
            child_ids.append(child_id)
            all_chunks.append(child_chunk)

        parent_chunk.children_ids = child_ids
        all_chunks.append(parent_chunk)

    return all_chunks


def _chunk_sentence_window(doc: RawDocument) -> list[Chunk]:
    """Per-sentence chunks; each carries a surrounding window in *window_text*."""
    sentences = _split_sentences(doc.text)
    n = cfg.sentence_window_size
    all_chunks: list[Chunk] = []

    for i, sentence in enumerate(sentences):
        window_start = max(0, i - n)
        window_end = min(len(sentences), i + n + 1)
        window_text = " ".join(sentences[window_start:window_end])

        chunk = Chunk(
            id=_new_id(),
            source_path=doc.path,
            text=sentence,
            chunk_type=ChunkType.SENTENCE_WINDOW,
            metadata={**doc.metadata, "sentence_index": i},
            parent_id=None,
            window_text=window_text,
        )
        all_chunks.append(chunk)

    return all_chunks


def _chunk_hierarchical(doc: RawDocument) -> list[Chunk]:
    """Two-level hierarchical chunking: paragraph-grouped summaries → children."""
    # Level 1: group paragraphs into large summary chunks
    paragraphs = [p.strip() for p in doc.text.split("\n\n") if p.strip()]

    # Build L1 chunks by greedily accumulating paragraphs up to parent_chunk_size
    l1_texts: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        # +2 for the "\n\n" separator we'd join with
        added_len = len(para) + (2 if current_parts else 0)
        if current_parts and current_len + added_len > cfg.parent_chunk_size:
            l1_texts.append("\n\n".join(current_parts))
            current_parts = [para]
            current_len = len(para)
        else:
            current_parts.append(para)
            current_len += added_len

    if current_parts:
        l1_texts.append("\n\n".join(current_parts))

    all_chunks: list[Chunk] = []

    for l1_text in l1_texts:
        l1_id = _new_id()
        l1_chunk = Chunk(
            id=l1_id,
            source_path=doc.path,
            text=l1_text,
            chunk_type=ChunkType.PARENT,
            metadata={**doc.metadata},
        )

        child_texts = _split_text(l1_text, cfg.chunk_size, cfg.chunk_overlap)
        child_ids: list[str] = []

        for child_text in child_texts:
            child_id = _new_id()
            child_chunk = Chunk(
                id=child_id,
                source_path=doc.path,
                text=child_text,
                chunk_type=ChunkType.CHILD,
                metadata={**doc.metadata},
                parent_id=l1_id,
            )
            child_ids.append(child_id)
            all_chunks.append(child_chunk)

        l1_chunk.children_ids = child_ids
        all_chunks.append(l1_chunk)

    return all_chunks


# ---------------------------------------------------------------------------
# Image chunks
# ---------------------------------------------------------------------------

def _image_chunks(doc: RawDocument) -> list[Chunk]:
    """Create one chunk per image in *doc.images*."""
    chunks: list[Chunk] = []
    for i, image_bytes in enumerate(doc.images):
        chunk = Chunk(
            id=_new_id(),
            source_path=doc.path,
            text="",
            chunk_type=ChunkType.TEXT,
            metadata={**doc.metadata, "is_image": True, "image_index": i},
            image_data=image_bytes,
        )
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_STRATEGIES = {
    "recursive": _chunk_recursive,
    "sentence_window": _chunk_sentence_window,
    "hierarchical": _chunk_hierarchical,
}


def chunk(doc: RawDocument, strategy: Optional[str] = None) -> list[Chunk]:
    """Chunk a RawDocument using the configured strategy.

    Args:
        doc:      The document to chunk.
        strategy: Override ``cfg.chunk_strategy`` if given.  Must be one of
                  ``"recursive"``, ``"sentence_window"``, or
                  ``"hierarchical"``.

    Returns:
        All chunks (text chunks produced by the strategy) combined with one
        image chunk per entry in ``doc.images``.

    Raises:
        ValueError: If *strategy* (or ``cfg.chunk_strategy``) is not
                    a recognised strategy name.
    """
    resolved = strategy if strategy is not None else cfg.chunk_strategy
    strategy_fn = _STRATEGIES.get(resolved)
    if strategy_fn is None:
        raise ValueError(
            f"Unknown chunking strategy {resolved!r}. "
            f"Choose from: {list(_STRATEGIES)}"
        )

    text_chunks = strategy_fn(doc)
    image_chunks = _image_chunks(doc)
    return text_chunks + image_chunks
