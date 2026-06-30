import logging
import time

from django.conf import settings

from .endpoints import rerank_url, score_url
from .errors import LLMProviderError
from .http_client import json_request

logger = logging.getLogger(__name__)


class RerankerClient:
    def __init__(self):
        self.model = settings.RERANKER_MODEL
        self.base_url = settings.RERANKER_BASE_URL
        self.api_key = settings.RERANKER_API_KEY

    def rerank(self, query: str, documents: list[str], top_k: int = None) -> list[dict]:  # noqa: RUF013
        top_k = top_k or len(documents)

        # Try /score (vLLM native) first, fall back to /v1/rerank (Cohere-compatible)
        score_payload = {
            "model": self.model,
            "text_1": [query] * len(documents),
            "text_2": documents,
        }
        rerank_payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_k,
        }

        start = time.time()
        try:
            try:
                url = score_url(self.base_url)
                data = json_request(url, score_payload, api_key=self.api_key)
                scored_items = [
                    (item["index"], item.get("score", 0.0))
                    for item in data.get("data", [])
                ]
            except LLMProviderError as e:
                if getattr(e, "status_code", None) != 404:  # noqa: PLR2004
                    raise
                url = rerank_url(self.base_url)
                data = json_request(url, rerank_payload, api_key=self.api_key)
                scored_items = [
                    (item["index"], item.get("relevance_score", 0.0))
                    for item in data.get("results", [])
                ]

            scored_items.sort(key=lambda x: x[1], reverse=True)
            duration = time.time() - start
            logger.info("[TIMING] reranker=%.3fs model=%s docs=%d", duration, self.model, len(documents))
            results = []
            for idx, score in scored_items[:top_k]:
                results.append({
                    "index": idx,
                    "relevance_score": score,
                    "document": documents[idx],
                })
            return results  # noqa: TRY300
        except LLMProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            duration = time.time() - start
            logger.error("Rerank failed after %.3fs: %s", duration, e)  # noqa: TRY400
            raise LLMProviderError(str(e))  # noqa: B904

