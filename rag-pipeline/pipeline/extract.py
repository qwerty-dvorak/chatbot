from pathlib import Path

import fitz  # PyMuPDF

from .config import cfg
from .models import ContentType, RawDocument

_TEXT_EXTENSIONS = {".txt", ".md", ".rst"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _extract_text(path: Path) -> RawDocument:
    text = path.read_text(encoding="utf-8", errors="replace")
    metadata = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
    }
    return RawDocument(
        path=str(path),
        content_type=ContentType.TEXT,
        text=text,
        images=[],
        metadata=metadata,
    )


def _extract_pdf(path: Path) -> RawDocument:
    """Render each PDF page as a PNG image at cfg.ocr_pdf_dpi.

    Text is left empty — OCR happens in the ingest pipeline via PaddleOCR-VL.
    Each element of images is (png_bytes, ""), page text populated later by OCR.
    """
    doc = fitz.open(str(path))
    images: list[tuple[bytes, str]] = []
    matrix = fitz.Matrix(cfg.ocr_pdf_dpi / 72, cfg.ocr_pdf_dpi / 72)

    for page in doc:
        try:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            images.append((pix.tobytes("png"), ""))
        except Exception:
            images.append((b"", ""))

    page_count = doc.page_count
    doc.close()

    metadata = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "page_count": page_count,
    }
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="",
        images=images,
        metadata=metadata,
    )


<<<<<<< Updated upstream
def _extract_pdf_text_only(path: Path) -> RawDocument:
    """Extract a PDF text layer without enumerating embedded images."""
    reader = pypdf.PdfReader(str(path))
    pages_text: list[str] = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            pages_text.append("")
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="\n\n".join(pages_text),
        images=[],
        metadata={
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "page_count": len(reader.pages),
        },
=======
def _extract_pdf_at_dpi(path: Path, dpi: int) -> RawDocument:
    """Render each PDF page as a PNG at an explicit *dpi*, ignoring cfg.ocr_pdf_dpi."""
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

    metadata = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "page_count": page_count,
    }
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text="",
        images=images,
        metadata=metadata,
    )


def _extract_pdf_fast(path: Path) -> RawDocument:
    """Extract text from a PDF using PyMuPDF's built-in text layer — no OCR, no images.

    Suitable for the instant tier where latency matters more than quality.
    For scanned PDFs with no text layer, the returned text will be empty or
    sparse; callers should fall back to OCR-based extraction in that case.

    Returns a RawDocument with ``images=[]`` so the ingest pipeline skips
    both Track B (multimodal embedding) and OCR.
    """
    doc = fitz.open(str(path))
    pages_text: list[str] = []

    for page in doc:
        try:
            t = page.get_text("text")
            pages_text.append(t)
        except Exception:
            pages_text.append("")

    full_text = "\n\n".join(t for t in pages_text if t.strip())
    page_count = doc.page_count
    doc.close()

    metadata = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "page_count": page_count,
        "extraction_method": "pymupdf_text",
    }
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text=full_text,
        images=[],
        metadata=metadata,
>>>>>>> Stashed changes
    )


def _extract_image(path: Path) -> RawDocument:
    raw_bytes = path.read_bytes()
    metadata = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
    }
    return RawDocument(
        path=str(path),
        content_type=ContentType.IMAGE,
        text="",
        images=[(raw_bytes, "")],
        metadata=metadata,
    )


def extract(path: str) -> RawDocument:
    """Detect file type and extract content.

    - ``.txt``, ``.md``, ``.rst`` — plain text read directly
    - ``.pdf``                    — pages rendered as PNG images (OCR happens in ingest)
    - image extensions            — raw bytes, no text
    - everything else             — treated as plain text
    """
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
    """Extract content with minimal latency — suitable for instant-tier ingestion.

    PDFs use PyMuPDF's text layer instead of rendering + OCR.  Images and
    text files are handled identically to :func:`extract`.
    """
    p = Path(path)
    ext = p.suffix.lower()

    if ext in _TEXT_EXTENSIONS:
        return _extract_text(p)
    if ext == ".pdf":
        return _extract_pdf_fast(p)
    if ext in _IMAGE_EXTENSIONS:
        return _extract_image(p)

    return _extract_text(p)


def extract_fast(path: str) -> RawDocument:
    """Extract text without PDF image enumeration for latency-sensitive ingestion."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return _extract_pdf_text_only(p)
    return extract(path)
