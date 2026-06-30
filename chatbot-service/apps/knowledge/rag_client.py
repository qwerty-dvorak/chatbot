import logging
import os
import time
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RagApiClient:
    """Client for the RAG Pipeline API."""

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

    def ingest(self, file_path: str, ocr_mode: str | None = None,
               document_reference_id: str | None = None,
               generate_summary: bool = True) -> dict | None:
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
                data = {}
                if ocr_mode:
                    data["ocr_mode"] = ocr_mode
                if document_reference_id:
                    data["document_id"] = document_reference_id
                if not generate_summary:
                    data["skip_summary"] = "true"
                resp = self._timed_request("POST", f"{self.base_url}/v1/ingest", files=files, data=data, timeout=self.timeout)
                return resp.json()
        except requests.RequestException as exc:
            duration = time.time() - start
            logger.error("RAG API ingest failed after %.3fs: %s", duration, exc)  # noqa: TRY400
            return None

    def get_job(self, job_id: str) -> dict | None:
        try:
            resp = self._timed_request("GET", f"{self.base_url}/v1/ingestions/{job_id}", timeout=10)
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("RAG API get_job failed: %s", exc)
            return None

    def list_jobs(self, status: str | None = None, limit: int = 200) -> list[dict]:
        try:
            params = {"limit": limit}
            if status:
                params["status"] = status
            resp = self._timed_request(
                "GET",
                f"{self.base_url}/v1/ingestions",
                params=params,
                timeout=10,
            )
            return resp.json().get("jobs", [])
        except requests.RequestException as exc:
            logger.warning("RAG API list_jobs failed: %s", exc)
            return []

    def poll_job(self, job_id: str, max_retries: int = 120, interval: float = 1.0) -> dict | None:
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

    def cancel_job(self, job_id: str) -> bool:
        try:
            resp = self._timed_request("DELETE", f"{self.base_url}/v1/ingestions/{job_id}", timeout=10)
            return resp.status_code == 200  # noqa: PLR2004
        except requests.RequestException as exc:
            logger.warning("RAG API cancel_job failed: %s", exc)
            return False

    def _timed_request(self, method: str, url: str, **kwargs) -> requests.Response:
        start = time.time()
        try:
            resp = requests.request(method, url, **kwargs)  # noqa: S113
            resp.raise_for_status()
            duration = time.time() - start
            logger.info("[TIMING] rag_api_%s %.3fs %s %s", method.lower(), duration, resp.status_code, url)
            return resp
        except requests.RequestException:
            duration = time.time() - start
            logger.warning("[TIMING] rag_api_%s %.3fs FAILED %s", method.lower(), duration, url)
            raise

    def search(self, query: str, top_k: int = 5, artifact_ids: list[str] | None = None,
               artifact_sources: list[str] | None = None,
               use_reranker: bool | None = None,
               hyde: bool | None = None, sub_queries: bool | None = None,
               stepback: bool | None = None) -> dict:
        if not self.enabled:
            return {"results": [], "enhanced_queries": []}
        body = {"query": query, "top_k": top_k}
        if artifact_ids:
            body["artifact_ids"] = artifact_ids
        if artifact_sources:
            body["artifact_sources"] = artifact_sources
        if use_reranker is not None:
            body["use_reranker"] = use_reranker
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
            logger.error("RAG API search failed: %s", exc)  # noqa: TRY400
            return {"results": [], "enhanced_queries": []}


rag_client = RagApiClient()
