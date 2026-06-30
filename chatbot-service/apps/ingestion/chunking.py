"""Text chunking for document ingestion."""

import re

from django.conf import settings


class TextChunker:
    def __init__(self, target_tokens=None, overlap_tokens=None) -> None:
        self.target_tokens = target_tokens or settings.RAG_CHUNK_TARGET_TOKENS
        self.overlap_tokens = overlap_tokens or settings.RAG_CHUNK_OVERLAP_TOKENS

    def chunk(self, text: str) -> list[dict]:
        if not text:
            return []
        paragraphs = self._split_paragraphs(text)
        chunks = []
        current_chunk = ""
        chunk_index = 0
        for para in paragraphs:
            if self._estimate_tokens(current_chunk + para) > self.target_tokens and current_chunk:
                chunks.append(self._make_chunk(current_chunk, chunk_index))
                chunk_index += 1
                current_chunk = self._get_overlap(current_chunk)
            current_chunk += para + "\n\n"
        if current_chunk.strip():
            chunks.append(self._make_chunk(current_chunk, chunk_index))
        return chunks

    def _split_paragraphs(self, text: str) -> list[str]:
        paragraphs = re.split(r"\n\s*\n", text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def _get_overlap(self, text: str) -> str:
        words = text.split()
        overlap_words = max(1, self.overlap_tokens * 4)
        return " ".join(words[-overlap_words:]) if len(words) > overlap_words else ""

    def _make_chunk(self, content: str, index: int) -> dict:
        return {
            "chunk_index": index,
            "content": content.strip(),
            "token_count": self._estimate_tokens(content),
        }
