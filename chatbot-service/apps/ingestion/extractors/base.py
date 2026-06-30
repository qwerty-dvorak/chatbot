"""Abstract base extractor for document text extraction."""

from abc import ABC, abstractmethod
from typing import Any


class BaseExtractor(ABC):
    """Abstract base extractor for document text extraction."""

    @abstractmethod
    def can_handle(self, mime_type: str) -> bool:
        """Check if this extractor can handle the given MIME type."""
        ...

    @abstractmethod
    def extract(self, file_path: str, mime_type: str, extracted_text: str = "") -> dict[str, Any]:
        """Extract text content from a file."""
        ...

    def extract_text(self, file_path: str, mime_type: str, extracted_text: str = "") -> str:
        """Extract text and return the text portion."""
        result = self.extract(file_path, mime_type, extracted_text)
        return result.get("text", "")
