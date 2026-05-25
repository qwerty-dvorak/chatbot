from abc import ABC, abstractmethod
from typing import Any

from apps.knowledge.models import Document


class BaseExtractor(ABC):
    @abstractmethod
    def can_handle(self, mime_type: str) -> bool:
        ...

    @abstractmethod
    def extract(self, document: Document) -> dict[str, Any]:
        ...

    def extract_text(self, document: Document) -> str:
        result = self.extract(document)
        return result.get("text", "")
