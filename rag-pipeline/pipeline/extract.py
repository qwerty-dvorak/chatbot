"""File extraction — text, PDF, and image document parsing."""

from __future__ import annotations

from pathlib import Path

import fitz

from .config import cfg
from .models import ContentType, RawDocument

_TEXT_EXTENSIONS = {".txt", ".md", ".rst"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _text(path: Path) -> RawDocument:
    """Extract text from a plain-text file."""
    return RawDocument(
        path=str(path),
        content_type=ContentType.TEXT,
        text=path.read_text(encoding="utf-8", errors="replace"),
        images=[],
        metadata={"filename": path.name, "extension": path.suffix.lower(), "size_bytes": path.stat().st_size},
    )


def _pdf_at_dpi(path: Path, dpi: int) -> RawDocument:
    """Extract PDF pages as images at the given DPI."""
    doc = fitz.open(str(path))
    images = []
    for page in doc:
        try:
            images.append(page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False).tobytes("png"))
        except RuntimeError:
            images.append(b"")
    count = doc.page_count
    doc.close()
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="",
        images=images,
        metadata={"filename": path.name, "extension": ".pdf", "size_bytes": path.stat().st_size, "page_count": count},
    )


def _pdf_fast(path: Path) -> RawDocument:
    """Extract PDF text directly via PyMuPDF (no OCR)."""
    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        try:
            pages.append(page.get_text("text") or "")
        except RuntimeError:
            pages.append("")
    count = doc.page_count
    doc.close()
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="\n\n".join(t for t in pages if t.strip()),
        images=[],
        metadata={
            "filename": path.name,
            "extension": ".pdf",
            "size_bytes": path.stat().st_size,
            "page_count": count,
            "extraction_method": "pymupdf_text",
        },
    )


def _image(path: Path) -> RawDocument:
    """Extract an image file."""
    return RawDocument(
        path=str(path),
        content_type=ContentType.IMAGE,
        text="",
        images=[path.read_bytes()],
        metadata={"filename": path.name, "extension": path.suffix.lower(), "size_bytes": path.stat().st_size},
    )


def extract(path: str, *, fast: bool = False) -> RawDocument:
    """Extract a document from a file path."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return _text(p)
    if ext == ".pdf":
        return _pdf_fast(p) if fast else _pdf_at_dpi(p, cfg.ocr_pdf_dpi)
    if ext in _IMAGE_EXTENSIONS:
        return _image(p)
    return _text(p)
