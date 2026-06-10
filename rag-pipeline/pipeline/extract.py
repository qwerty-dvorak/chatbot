import io
from pathlib import Path

from PIL import Image
import pypdf

from .models import ContentType, RawDocument

# Extensions treated as plain text
_TEXT_EXTENSIONS = {".txt", ".md", ".rst"}

# Extensions treated as images
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _extract_text(path: Path) -> RawDocument:
    """Read a plain-text file."""
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


def _page_to_png_bytes(page: pypdf.PageObject) -> bytes:
    """Render a pypdf page to PNG bytes via PIL.

    pypdf >= 4 exposes ``page.images`` which yields ``ImageFile`` objects
    whose ``.data`` attribute holds the raw compressed image bytes.  We
    decode the first available image on the page and re-encode it as PNG so
    the caller always receives a consistent format.

    Returns an empty ``bytes`` object when no renderable image is found.
    """
    try:
        page_images = list(page.images)
    except Exception:
        return b""

    if not page_images:
        return b""

    # Composite all images on the page into one PNG by using the first image.
    # For richer rendering, callers may replace this with a pdf2image-based
    # approach; we stay stdlib-adjacent here.
    try:
        raw_data = page_images[0].data
        img = Image.open(io.BytesIO(raw_data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return b""


def _extract_pdf(path: Path) -> RawDocument:
    """Extract text and images from a PDF file."""
    reader = pypdf.PdfReader(str(path))
    pages_text: list[str] = []
    images: list[bytes] = []

    for page in reader.pages:
        # Text
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        pages_text.append(page_text)

        # Images — best-effort; silently skip on any error
        png_bytes = _page_to_png_bytes(page)
        if png_bytes:
            images.append(png_bytes)

    text = "\n\n".join(pages_text)
    metadata = {
        "filename": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "page_count": len(reader.pages),
    }
    return RawDocument(
        path=str(path),
        content_type=ContentType.PDF,
        text=text,
        images=images,
        metadata=metadata,
    )


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
    )


def _extract_image(path: Path) -> RawDocument:
    """Read an image file as raw bytes; no text extraction."""
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
        images=[raw_bytes],
        metadata=metadata,
    )


def extract(path: str) -> RawDocument:
    """Detect file type by extension and extract text + images.

    Supported types:
    - ``.txt``, ``.md``, ``.rst`` — plain text, no images
    - ``.pdf``                    — text via pypdf; images from page.images (pypdf >= 4)
    - ``.png``, ``.jpg``, ``.jpeg``, ``.gif``, ``.bmp``, ``.webp`` — raw bytes, no text
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

    # Unknown extension — fall back to plain-text extraction
    return _extract_text(p)


def extract_fast(path: str) -> RawDocument:
    """Extract text without PDF image enumeration for latency-sensitive ingestion."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return _extract_pdf_text_only(p)
    return extract(path)
