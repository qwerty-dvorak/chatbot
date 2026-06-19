from typing import Any

from .base import BaseExtractor


class TextExtractor(BaseExtractor):
    def can_handle(self, mime_type: str) -> bool:
        return mime_type in (
            "text/plain", "text/markdown", "text/csv",
            "application/xml", "text/html",
        )

    def extract(self, file_path: str, mime_type: str, extracted_text: str = "") -> dict[str, Any]:
        content = extracted_text
        if not content and file_path:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except FileNotFoundError:
                pass
        return {
            "text": content or "",
            "metadata": {"parser": "text", "length": len(content or "")},
        }