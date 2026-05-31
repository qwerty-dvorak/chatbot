import logging

from django.conf import settings

from .errors import LLMProviderError

logger = logging.getLogger(__name__)


class RerankerClient:
    def __init__(self):
        self.model = settings.RERANKER_MODEL
        self.base_url = settings.RERANKER_BASE_URL
        self.api_key = settings.RERANKER_API_KEY

    def rerank(self, query: str, documents: list[str], top_k: int = None) -> list[dict]:
        try:
            from litellm import rerank
        except ImportError:
            raise LLMProviderError("litellm is not installed")

        try:
            response = rerank(
                model=self.model,
                query=query,
                documents=documents,
                top_k=top_k or len(documents),
                api_base=self.base_url,
                api_key=self.api_key,
            )
            results = []
            for r in response.results:
                results.append({
                    "index": r.index,
                    "relevance_score": r.relevance_score,
                    "document": documents[r.index],
                })
            return results
        except Exception as e:
            logger.error(f"Rerank failed: {e}")
            raise LLMProviderError(str(e))


class FakeRerankerClient:
    def rerank(self, query, documents, top_k=None):
        scores = []
        for i, doc in enumerate(documents):
            overlap = len(set(query.lower().split()) & set(doc.lower().split()))
            scores.append((i, overlap / max(len(query.split()), 1)))
        scores.sort(key=lambda x: x[1], reverse=True)
        top_k = top_k or len(documents)
        return [
            {"index": idx, "relevance_score": score, "document": documents[idx]}
            for idx, score in scores[:top_k]
        ]
