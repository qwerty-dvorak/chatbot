from pathlib import Path

import fitz  # PyMuPDF

from .config import cfg
from .models import ContentType, RawDocument

_TEXT_EXTENSIONS = {".txt", ".md", ".rst"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _metadata(path: Path, **extra: object) -> dict:
    return {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        **extra,
    }


def _extract_text(path: Path) -> RawDocument:
    return RawDocument(
        path=str(path),
        content_type=ContentType.TEXT,
        text=path.read_text(encoding="utf-8", errors="replace"),
        images=[],
        metadata=_metadata(path),
    )


def _extract_pdf_at_dpi(path: Path, dpi: int) -> RawDocument:
    """Render each PDF page as a PNG at the requested DPI."""
    doc = fitz.open(str(path))
    images: list[tuple[bytes, str]] = []
    matrix = fitz.Matrix(dpi / 72, dpi / 72)

    for page in doc:
        try:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            images.append((pix.tobytes("png"), ""))
        except Exception:
            images.append((b"", ""))

    page_count = doc.page_count
    doc.close()
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="",
        images=images,
        metadata=_metadata(path, page_count=page_count),
    )


def _extract_pdf(path: Path) -> RawDocument:
    """Render PDF pages for OCR-based ingestion."""
    return _extract_pdf_at_dpi(path, cfg.ocr_pdf_dpi)


def _extract_pdf_fast(path: Path) -> RawDocument:
    """Extract PDF text layer without rendering images or OCR."""
    doc = fitz.open(str(path))
    pages_text: list[str] = []
    for page in doc:
        try:
            pages_text.append(page.get_text("text") or "")
        except Exception:
            pages_text.append("")

    page_count = doc.page_count
    doc.close()
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="\n\n".join(text for text in pages_text if text.strip()),
        images=[],
        metadata=_metadata(path, page_count=page_count, extraction_method="pymupdf_text"),
    )


def _extract_image(path: Path) -> RawDocument:
    return RawDocument(
        path=str(path),
        content_type=ContentType.IMAGE,
        text="",
        images=[(path.read_bytes(), "")],
        metadata=_metadata(path),
    )


def extract(path: str) -> RawDocument:
    """Detect file type and extract content for full-quality ingestion."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return _extract_text(p)
    if ext == ".pdf":
        return _extract_pdf(p)
    if ext in _IMAGE_EXTENSIONS:
        return _extract_image(p)
    return _extract_text(p)


def extract_fast(path: str) -> RawDocument:
    """Extract content with minimal latency for instant-tier ingestion."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return _extract_text(p)
    if ext == ".pdf":
        return _extract_pdf_fast(p)
    if ext in _IMAGE_EXTENSIONS:
        return _extract_image(p)
    return _extract_text(p)
