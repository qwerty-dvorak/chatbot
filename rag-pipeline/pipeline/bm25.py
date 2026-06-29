"""BM25 indexing and search using rank_bm25."""

from __future__ import annotations

import pickle
import tempfile
from pathlib import Path

from rank_bm25 import BM25Okapi

from .config import cfg
from .models import Chunk  # noqa: TC001


def build_index(chunks: list[Chunk]) -> BM25Okapi:
    """Build a BM25 index from a list of chunks."""
    tokenized = [c.text.lower().split() for c in chunks]
    bm25 = BM25Okapi(tokenized)
    _save(bm25, chunks)
    return bm25


def load_index() -> tuple[BM25Okapi, list[Chunk]]:
    """Load a BM25 index from disk."""
    path = Path(cfg.bm25_index_path)
    if not path.exists():
        msg = f"BM25 index not found at {path!r}"
        raise FileNotFoundError(msg)
    data = pickle.load(path.open("rb"))  # noqa: S301
    return data["bm25"], data["chunks"]


def _save(bm25: BM25Okapi, chunks: list[Chunk]) -> None:
    """Persist a BM25 index to disk atomically."""
    path = Path(cfg.bm25_index_path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    _, tmp = tempfile.mkstemp(prefix=".bm25-", dir=str(directory))
    try:
        with Path(tmp).open("wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)
            f.flush()
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
