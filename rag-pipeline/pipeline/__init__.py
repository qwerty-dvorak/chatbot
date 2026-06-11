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
    "db",
    "object_store",
    "text_pipeline",
    "image_pipeline",
]


def __getattr__(name: str):
    if name == "ingest_path":
        from .ingest import ingest_path

        return ingest_path
    if name in {"search", "format_results"}:
        from .search import format_results, search

        return {"search": search, "format_results": format_results}[name]
    if name == "db":
        from . import db

        return db
    if name == "object_store":
        from . import object_store

        return object_store
    if name == "text_pipeline":
        from . import text_pipeline

        return text_pipeline
    if name == "image_pipeline":
        from . import image_pipeline

        return image_pipeline
    raise AttributeError(name)
