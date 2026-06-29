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
    "Chunk",
    "ChunkType",
    "ContentType",
    "EmbeddedChunk",
    "IngestionTier",
    "RawDocument",
    "SearchResult",
    "cfg",
    "db",
    "format_results",
    "ingest_path",
    "object_store",
    "search",
]


def __getattr__(name: str) -> object:
    if name in {"ingest_path", "search", "format_results"}:
        mappings = {"ingest_path": ".ingest", "search": ".search", "format_results": ".search"}
        mod = importlib.import_module(mappings[name], __package__)
        return getattr(mod, name if name != "format_results" else "format_results")
    if name in {"db", "object_store"}:
        return importlib.import_module(f".{name}", __package__)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
