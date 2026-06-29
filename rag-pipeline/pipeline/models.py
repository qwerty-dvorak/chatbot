"""Data models for the RAG pipeline."""

from dataclasses import dataclass, field
from enum import Enum


class IngestionTier(str, Enum):
    """
    Processing depth for document ingestion.

    INSTANT — text-only, no OCR, no multimodal embedding.  Target: < 2 s per
              page.  For chat-session file uploads that need immediate RAG.
    SLOW    — full OCR + both text and multimodal embedding for one file.
              Includes HyDE at search time and light hypothetical-question
              augmentation at index time.
    """

    INSTANT = "instant"
    SLOW = "slow"


class ChunkType(str, Enum):
    """Types of chunks in the index."""

    TEXT = "text"
    IMAGE = "image"
    PARENT = "parent"
    CHILD = "child"
    SENTENCE_WINDOW = "sentence_window"
    SUMMARY = "summary"
    HYPOTHETICAL_QUESTION = "hypothetical_question"


class ContentType(str, Enum):
    """Content types for raw documents."""

    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"


@dataclass
class RawDocument:
    """A raw document extracted from a file."""

    path: str
    content_type: ContentType
    text: str  # full extracted text
    images: list[bytes] = field(default_factory=list)  # raw PNG bytes
    metadata: dict = field(default_factory=dict)


@dataclass
class Chunk:
    """A single chunk of text or image data."""

    id: str  # uuid4 hex
    source_path: str
    text: str
    chunk_type: ChunkType
    metadata: dict = field(default_factory=dict)
    parent_id: str | None = None  # set for CHILD and SENTENCE_WINDOW
    window_text: str | None = None  # surrounding context for SENTENCE_WINDOW
    children_ids: list[str] = field(default_factory=list)
    image_data: bytes | None = None  # raw image bytes if this is an image chunk


@dataclass
class EmbeddedChunk:
    """A chunk with its embedding vector."""

    chunk: Chunk
    embedding: list[float]
    is_multimodal: bool = False


@dataclass
class SearchResult:
    """A search result with score and retrieval metadata."""

    chunk: Chunk
    score: float
    rank: int
    retrieval_method: str  # "vector", "bm25", "hybrid", "reranked"
