from abc import ABC, abstractmethod
from typing import Any


class BaseExtractor(ABC):
    @abstractmethod
    def can_handle(self, mime_type: str) -> bool:
        ...

    @abstractmethod
    def extract(self, file_path: str, mime_type: str, extracted_text: str = "") -> dict[str, Any]:
        ...

    def extract_text(self, file_path: str, mime_type: str, extracted_text: str = "") -> str:
        result = self.extract(file_path, mime_type, extracted_text)
        return result.get("text", "")