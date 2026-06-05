from typing import Any

from apps.knowledge.models import Document

from .base import BaseExtractor


class ImageExtractor(BaseExtractor):
    def can_handle(self, mime_type: str) -> bool:
        return mime_type in ("image/png", "image/jpeg", "image/webp")

    def extract(self, document: Document) -> dict[str, Any]:
        return {
            "text": document.extracted_text or "",
            "description": "",
            "metadata": {"parser": "image_basic"},
        }
