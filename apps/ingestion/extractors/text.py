from typing import Any

from apps.knowledge.models import Document

from .base import BaseExtractor


class TextExtractor(BaseExtractor):
    def can_handle(self, mime_type: str) -> bool:
        return mime_type in (
            "text/plain", "text/markdown", "text/csv",
            "application/xml", "text/html",
        )

    def extract(self, document: Document) -> dict[str, Any]:
        if document.file and document.file != "":
            from django.core.files.storage import default_storage
            try:
                content = default_storage.open(document.file).read().decode("utf-8", errors="replace")
            except FileNotFoundError:
                content = document.extracted_text or ""
        else:
            content = document.extracted_text or ""
        return {
            "text": content,
            "metadata": {"parser": "text", "length": len(content)},
        }
