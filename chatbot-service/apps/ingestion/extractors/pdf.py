from typing import Any

from .base import BaseExtractor


class PDFExtractor(BaseExtractor):
    def can_handle(self, mime_type: str) -> bool:
        return mime_type == "application/pdf"

    def extract(self, file_path: str, mime_type: str, extracted_text: str = "") -> dict[str, Any]:
        return {
            "text": extracted_text or "",
            "pages": [],
            "metadata": {"parser": "pdf_basic", "page_count": 0},
        }