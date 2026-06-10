"""Public package API with lazy imports for heavyweight pipeline modules."""

from .config import cfg
from .models import (
    Chunk,
    ChunkType,
    ContentType,
    EmbeddedChunk,
    IngestionTier,
    RawDocument,
    SearchResult,
)

__all__ = [
    "RawDocument",
    "Chunk",
    "EmbeddedChunk",
    "SearchResult",
    "ChunkType",
    "ContentType",
    "IngestionTier",
    "cfg",
    "ingest_path",
    "search",
    "format_results",
]


def __getattr__(name: str):
    if name == "ingest_path":
        from .ingest import ingest_path

        return ingest_path
    if name in {"search", "format_results"}:
        from .search import format_results, search

        return {"search": search, "format_results": format_results}[name]
    raise AttributeError(name)
