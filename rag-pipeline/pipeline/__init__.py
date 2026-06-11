"""Public package API with lazy imports for heavyweight pipeline modules."""

import importlib

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
    if name in {"ingest_path", "search", "format_results"}:
        _lazy = {"ingest_path": ".ingest", "search": ".search", "format_results": ".search"}
        mod = importlib.import_module(_lazy[name], __package__)
        return getattr(mod, name if name != "format_results" else "format_results")
    if name in {"db", "object_store", "text_pipeline", "image_pipeline"}:
        return importlib.import_module(f".{name}", __package__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
