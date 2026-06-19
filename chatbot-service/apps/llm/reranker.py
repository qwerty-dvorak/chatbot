import json
import logging
import time
import urllib.error
import urllib.request

from django.conf import settings

from .errors import LLMProviderError

logger = logging.getLogger(__name__)


class RerankerClient:
    def __init__(self):
        self.model = settings.RERANKER_MODEL
        self.base_url = settings.RERANKER_BASE_URL
        self.api_key = settings.RERANKER_API_KEY

    def rerank(self, query: str, documents: list[str], top_k: int = None) -> list[dict]:
        top_k = top_k or len(documents)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "opencode/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        base = self.base_url.rstrip("/")

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
                url = f"{base}/score"
                body = json.dumps(score_payload).encode()
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                resp = urllib.request.urlopen(req, timeout=120)
                data = json.loads(resp.read().decode())
                scored_items = [
                    (item["index"], item.get("score", 0.0))
                    for item in data.get("data", [])
                ]
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                url = f"{base}/v1/rerank"
                body = json.dumps(rerank_payload).encode()
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                resp = urllib.request.urlopen(req, timeout=120)
                data = json.loads(resp.read().decode())
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
            return results
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            logger.error("Rerank HTTP %d: %s", e.code, detail)
            raise LLMProviderError(detail, provider="openai", status_code=e.code)
        except urllib.error.URLError as e:
            logger.error("Rerank connection error: %s", e)
            raise LLMProviderError(str(e))
        except Exception as e:
            duration = time.time() - start
            logger.error("Rerank failed after %.3fs: %s", duration, e)
            raise LLMProviderError(str(e))

