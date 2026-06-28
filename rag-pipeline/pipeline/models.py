from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class IngestionTier(str, Enum):
    """Processing depth for document ingestion.

    INSTANT — text-only, no OCR, no multimodal embedding.  Target: < 2 s per
              page.  For chat-session file uploads that need immediate RAG.
    SLOW    — full OCR + both text and multimodal embedding for one file.
              Includes HyDE at search time and light hypothetical-question
              augmentation at index time.
    GLOBAL  — batch-mode, maximum quality.  Hierarchical chunking, full
              hypothetical-question augmentation, all query enhancements
              (HyDE + sub-queries + stepback) using the chatbot-service LLM.
    """
    INSTANT = "instant"
    SLOW    = "slow"
    GLOBAL  = "global"


class ChunkType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    PARENT = "parent"
    CHILD = "child"
    SENTENCE_WINDOW = "sentence_window"
    SUMMARY = "summary"
    HYPOTHETICAL_QUESTION = "hypothetical_question"


class ContentType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"


@dataclass
class RawDocument:
    path: str
    content_type: ContentType
    text: str                              # full extracted text
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (raw bytes, page text)
    metadata: dict = field(default_factory=dict)


@dataclass
class Chunk:
    id: str                                # uuid4 hex
    source_path: str
    text: str
    chunk_type: ChunkType
    metadata: dict = field(default_factory=dict)
    parent_id: Optional[str] = None        # set for CHILD and SENTENCE_WINDOW
    window_text: Optional[str] = None      # surrounding context for SENTENCE_WINDOW
    children_ids: list[str] = field(default_factory=list)
    image_data: Optional[bytes] = None     # raw image bytes if this is an image chunk


@dataclass
class EmbeddedChunk:
    chunk: Chunk
    embedding: list[float]
    is_multimodal: bool = False


@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    rank: int
    retrieval_method: str   # "vector", "bm25", "hybrid", "reranked"
