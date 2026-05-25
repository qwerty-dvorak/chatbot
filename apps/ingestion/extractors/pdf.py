from typing import Any

from apps.knowledge.models import Document

from .base import BaseExtractor


class PDFExtractor(BaseExtractor):
    def can_handle(self, mime_type: str) -> bool:
        return mime_type == "application/pdf"

    def extract(self, document: Document) -> dict[str, Any]:
        return {
            "text": document.extracted_text or "",
            "pages": [],
            "metadata": {"parser": "pdf_basic", "page_count": 0},
        }
