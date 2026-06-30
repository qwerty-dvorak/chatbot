"""Image extractor: basic metadata passthrough."""

from typing import Any

from .base import BaseExtractor


class ImageExtractor(BaseExtractor):
    def can_handle(self, mime_type: str) -> bool:
        return mime_type in ("image/png", "image/jpeg", "image/webp")

    def extract(self, file_path: str, mime_type: str, extracted_text: str = "") -> dict[str, Any]:
        return {
            "text": extracted_text or "",
            "description": "",
            "metadata": {"parser": "image_basic"},
        }
