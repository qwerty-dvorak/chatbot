import logging
import os
import time
import uuid
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RagApiClient:
    """Client for the RAG Pipeline API.

    Sends documents for ingestion and performs searches against the RAG API.
    Configured via environment variables prefixed with RAG_API_.
    """

    def __init__(self):
        self.base_url = os.environ.get(
            "RAG_API_BASE_URL",
            getattr(settings, "RAG_API_BASE_URL", "http://localhost:8093"),
        )
        self.timeout = int(os.environ.get("RAG_API_TIMEOUT", "300"))
        self.enabled = os.environ.get(
            "RAG_API_ENABLED",
            getattr(settings, "RAG_API_ENABLED", "false"),
        ).lower() in ("true", "1", "yes")

    def is_enabled(self) -> bool:
        return self.enabled

    def collection_stats(self, name: str | None = None) -> dict | None:
        """Get embedding metadata (model, dimension, count) for a Milvus collection."""
        try:
            if name:
                url = f"{self.base_url}/v1/collections/{name}/stats"
            else:
                url = f"{self.base_url}/v1/collections"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("RAG API collection stats failed: %s", exc)
            return None

    def health(self) -> dict | None:
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("RAG API health check failed: %s", exc)
            return None

    def ingest(self, file_path: str, tier: str = "slow", strategy: str | None = None) -> dict | None:
        """Upload a file to the RAG API and return the job response."""
        if not self.enabled:
            logger.info("RAG API disabled, skipping ingest for %s", file_path)
            return None
        path = Path(file_path)
        if not path.exists():
            logger.error("File not found: %s", file_path)
            return None
        start = time.time()
        try:
            with open(path, "rb") as f:
                files = {"files": (path.name, f)}
                data = {"tier": tier}
                if strategy:
                    data["strategy"] = strategy
                resp = self._timed_request("POST", f"{self.base_url}/v1/ingest", files=files, data=data, timeout=self.timeout)
                return resp.json()
        except requests.RequestException as exc:
            duration = time.time() - start
            logger.error("RAG API ingest failed after %.3fs: %s", duration, exc)
            return None

    def get_job(self, job_id: str) -> dict | None:
        """Fetch current job status from the RAG API (single request, no polling)."""
        try:
            resp = self._timed_request("GET", f"{self.base_url}/v1/ingestions/{job_id}", timeout=10)
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("RAG API get_job failed: %s", exc)
            return None

    def poll_job(self, job_id: str, max_retries: int = 120, interval: float = 1.0) -> dict | None:
        """Poll a job until completion or failure."""
        for _ in range(max_retries):
            try:
                resp = self._timed_request("GET", f"{self.base_url}/v1/ingestions/{job_id}", timeout=10)
                data = resp.json()
                status = data.get("status", "")
                if status in ("succeeded", "failed", "cancelled"):
                    return data
            except requests.RequestException as exc:
                logger.warning("RAG API poll failed: %s", exc)
            time.sleep(interval)
        logger.warning("RAG API poll timed out for job %s", job_id)
        return None

    def _timed_request(self, method: str, url: str, **kwargs) -> requests.Response:
        start = time.time()
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            duration = time.time() - start
            logger.info("[TIMING] rag_api_%s %.3fs %s %s", method.lower(), duration, resp.status_code, url)
            return resp
        except requests.RequestException:
            duration = time.time() - start
            logger.warning("[TIMING] rag_api_%s %.3fs FAILED %s", method.lower(), duration, url)
            raise

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               use_reranker: bool | None = None, tier: str | None = None,
               hierarchical: bool | None = None,
               hyde: bool | None = None, sub_queries: bool | None = None,
               stepback: bool | None = None) -> dict:
        """Search the RAG index and return the full response dict.

        Returns ``{"results": [...], "enhanced_queries": [...], ...}``
        or ``{"results": []}`` on error / when disabled.
        """
        if not self.enabled:
            return {"results": [], "enhanced_queries": []}
        body = {"query": query, "top_k": top_k, "mode": mode}
        if use_reranker is not None:
            body["use_reranker"] = use_reranker
        if tier is not None:
            body["tier"] = tier
        if hierarchical is not None:
            body["hierarchical"] = hierarchical
        if hyde is not None:
            body["hyde"] = hyde
        if sub_queries is not None:
            body["sub_queries"] = sub_queries
        if stepback is not None:
            body["stepback"] = stepback
        try:
            resp = self._timed_request("POST", f"{self.base_url}/v1/search", json=body, timeout=30)
            return resp.json()
        except requests.RequestException as exc:
            logger.error("RAG API search failed: %s", exc)
            return {"results": [], "enhanced_queries": []}


rag_client = RagApiClient()
