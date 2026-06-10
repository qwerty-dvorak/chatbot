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
        try:
            with open(path, "rb") as f:
                files = {"files": (path.name, f)}
                data = {"tier": tier}
                if strategy:
                    data["strategy"] = strategy
                resp = requests.post(
                    f"{self.base_url}/v1/ingest",
                    files=files,
                    data=data,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.json()
        except requests.RequestException as exc:
            logger.error("RAG API ingest failed: %s", exc)
            return None

    def poll_job(self, job_id: str, max_retries: int = 120, interval: float = 1.0) -> dict | None:
        """Poll a job until completion or failure."""
        for _ in range(max_retries):
            try:
                resp = requests.get(
                    f"{self.base_url}/v1/ingestions/{job_id}",
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "")
                if status in ("succeeded", "failed", "cancelled"):
                    return data
            except requests.RequestException as exc:
                logger.warning("RAG API poll failed: %s", exc)
            time.sleep(interval)
        logger.warning("RAG API poll timed out for job %s", job_id)
        return None

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               use_reranker: bool | None = None, tier: str | None = None) -> list[dict]:
        """Search the RAG index."""
        if not self.enabled:
            return []
        body = {"query": query, "top_k": top_k, "mode": mode}
        if use_reranker is not None:
            body["use_reranker"] = use_reranker
        if tier is not None:
            body["tier"] = tier
        try:
            resp = requests.post(
                f"{self.base_url}/v1/search",
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except requests.RequestException as exc:
            logger.error("RAG API search failed: %s", exc)
            return []


rag_client = RagApiClient()
