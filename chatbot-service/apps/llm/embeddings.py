import json
import logging
import time
import urllib.request

from django.conf import settings

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
        url = f"{self.base_url.rstrip('/')}/embeddings"
        body = json.dumps({"model": model, "input": texts}).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "opencode/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        start = time.time()
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read().decode())
            duration = time.time() - start
            embeddings = [item["embedding"] for item in data["data"]]
            logger.info("[TIMING] _embed %.3fs model=%s texts=%d dim=%d",
                        duration, model, len(texts), len(embeddings[0]) if embeddings else 0)
            return embeddings
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            logger.error("Embedding HTTP %d with %s: %s", e.code, model, detail)
            raise LLMProviderError(detail, provider="openai", status_code=e.code)
        except urllib.error.URLError as e:
            logger.error("Embedding connection error with %s: %s", model, e)
            raise LLMConnectionError(str(e))
        except (LLMConnectionError, LLMProviderError):
            raise
        except Exception as e:
            logger.error("Embedding failed with %s: %s", model, e)
            raise LLMProviderError(str(e))


