import logging
import time

from django.conf import settings

from .errors import LLMProviderError
from .token_usage import record_token_usage

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self):
        self.model = settings.QWEN_EMBEDDING_MODEL
        self.base_url = settings.LITELLM_BASE_URL
        self.api_key = settings.LITELLM_API_KEY

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            from litellm import embedding
        except ImportError:
            raise LLMProviderError("litellm is not installed")

        start = time.time()
        try:
            response = embedding(
                model=self.model,
                input=texts,
                api_base=self.base_url,
                api_key=self.api_key,
            )
            duration = time.time() - start
            embeddings = [item["embedding"] for item in response.data]
            self._log_usage(response, duration, len(texts))
            return embeddings
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            raise LLMProviderError(str(e))

    def embed_text(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def _log_usage(self, response, duration: float, num_texts: int):
        try:
            usage = getattr(response, "usage", None)
            if usage:
                record_token_usage(
                    operation="embedding",
                    model=self.model,
                    provider="litellm",
                    input_tokens=usage.prompt_tokens,
                    output_tokens=0,
                    total_tokens=usage.total_tokens,
                    metadata={"duration_s": round(duration, 3), "num_texts": num_texts},
                )
        except Exception as e:
            logger.warning(f"Failed to log embedding usage: {e}")


class FakeEmbeddingClient:
    def __init__(self, dim=256):
        self.dim = dim

    def embed(self, texts):
        import random
        random.seed(42)
        return [[random.random() for _ in range(self.dim)] for _ in texts]

    def embed_text(self, text):
        return self.embed([text])[0]
