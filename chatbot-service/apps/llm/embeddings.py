import logging
import time

from django.conf import settings

from .endpoints import embeddings_url
from .http_client import json_request
from .errors import LLMConnectionError, LLMProviderError

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self):
        self.text_model = settings.TEXT_EMBEDDING_MODEL
        self.multimodal_model = settings.MULTIMODAL_EMBEDDING_MODEL
        self.base_url = settings.EMBEDDING_BASE_URL
        self.api_key = settings.EMBEDDING_API_KEY

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self.text_model)

    def embed_text(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed_multimodal_text(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self.multimodal_model)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_document(self, text: str) -> list[float]:
        return self.embed_text(text)

    def _embed(self, texts: list[str], model: str) -> list[list[float]]:
        url = embeddings_url(self.base_url)
        body = {"model": model, "input": texts}
        start = time.time()
        try:
            data = json_request(url, body, api_key=self.api_key)
            duration = time.time() - start
            embeddings = [item["embedding"] for item in data["data"]]
            logger.info("[TIMING] _embed %.3fs model=%s texts=%d dim=%d",
                        duration, model, len(texts), len(embeddings[0]) if embeddings else 0)
            return embeddings
        except (LLMConnectionError, LLMProviderError):
            raise
        except Exception as e:
            logger.error("Embedding failed with %s: %s", model, e)
            raise LLMProviderError(str(e))


