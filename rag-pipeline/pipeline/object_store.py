"""Raw object store for the RAG pipeline.

Stores raw document bytes (PDF, text, images) on the filesystem, keyed by
a content-hash.  The live server only ever references objects by their store
key — the raw bytes live on disk.
"""

import hashlib
import os
import shutil
from pathlib import Path

from .config import cfg


def _store_root() -> Path:
    root = Path(cfg.object_store_path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _content_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store(data: bytes, suffix: str = "") -> str:
    """Store raw bytes and return the content-addressed key.

    Args:
        data:   Raw bytes to store.
        suffix: Optional file extension suffix (e.g. ``".pdf"``) so the
                stored path is human-readable.

    Returns:
        The SHA-256 hex digest used as the storage key.
    """
    key = _content_key(data)
    root = _store_root()
    prefix_dir = root / key[:2]
    prefix_dir.mkdir(parents=True, exist_ok=True)
    dest = prefix_dir / (key + suffix)
    if not dest.exists():
        tmp = dest.with_suffix(".tmp" + suffix)
        tmp.write_bytes(data)
        tmp.rename(dest)
    return key


def retrieve(key: str) -> bytes:
    """Retrieve raw bytes by store key.

    Args:
        key: The SHA-256 hex digest returned by :func:`store`.

    Returns:
        The raw bytes.

    Raises:
        FileNotFoundError: If *key* does not exist in the store.
    """
    root = _store_root()
    prefix_dir = root / key[:2]
    for f in prefix_dir.iterdir():
        if f.stem == key:
            return f.read_bytes()
    raise FileNotFoundError(f"Object not found in store: {key}")


def store_file(source_path: str) -> str:
    """Convenience: read a file from disk and store its bytes.

    Returns:
        The SHA-256 key.
    """
    data = Path(source_path).read_bytes()
    ext = Path(source_path).suffix.lower()
    return store(data, suffix=ext)


def delete(key: str) -> bool:
    """Delete an object from the store by key.

    Returns:
        True if the object was found and deleted, False otherwise.
    """
    root = _store_root()
    prefix_dir = root / key[:2]
    if not prefix_dir.is_dir():
        return False
    for f in prefix_dir.iterdir():
        if f.stem == key:
            f.unlink()
            return True
    return False


def store_path(key: str) -> str | None:
    """Return the filesystem path for a stored object, or None if absent."""
    root = _store_root()
    prefix_dir = root / key[:2]
    if not prefix_dir.is_dir():
        return None
    for f in prefix_dir.iterdir():
        if f.stem == key:
            return str(f)
    return None


def total_size_bytes() -> int:
    """Return the total size of all objects in the store."""
    root = _store_root()
    total = 0
    for dirpath, _, filenames in os.walk(str(root)):
        for fn in filenames:
            total += (Path(dirpath) / fn).stat().st_size
    return total


def clear() -> None:
    """Remove all objects from the store (destructive)."""
    root = _store_root()
    shutil.rmtree(str(root))
    root.mkdir(parents=True, exist_ok=True)
