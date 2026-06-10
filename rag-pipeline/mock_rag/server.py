"""
Stdlib-only mock server for the RAG Pipeline API.

Matches docs/api.md — exposes:
  POST /v1/ingest         (multipart file upload -> 202 + job)
  GET  /v1/ingestions     (list jobs)
  GET  /v1/ingestions/{id} (get one job)
  DELETE /v1/ingestions/{id} (cancel queued job)
  POST /v1/promote         (queue promotion)
  POST /v1/search          (synchronous search)
  GET  /health, /health/live, /health/ready
  GET  /v1/collections
  DELETE /v1/collections/{name}
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse


DATA_DIR = Path(os.environ.get("MOCK_RAG_DATA_DIR", "/tmp/mock_rag_data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
COLLECTIONS: list[str] = ["rag_text_chunks_mock", "rag_image_chunks_mock"]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _make_job_id() -> str:
    return uuid.uuid4().hex


def _find_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _public_job(job: dict) -> dict:
    payload = job["payload"]
    request = {k: v for k, v in payload.items() if k != "path"}
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "files": job["filenames"],
        "request": request,
        "result": job["result"],
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "completed_at": job["completed_at"],
        "links": {
            "self": f"/v1/ingestions/{job['id']}",
            "collection": "/v1/ingestions",
        },
    }


# ---------------------------------------------------------------------------
# Minimal multipart/form-data parser (no cgi module dependency)
# ---------------------------------------------------------------------------

def _parse_multipart(content_type: str, body: bytes) -> dict[str, list]:
    """Parse multipart/form-data body. Returns {field_name: [values]}."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"').strip("'").encode()

    if not boundary or not body:
        return {}

    parts = body.split(b"--" + boundary)
    result: dict[str, list] = {}

    for part in parts:
        if part.strip() in (b"", b"--", b"\r\n--"):
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = part[:header_end]
        data = part[header_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]

        content_disposition = ""
        name = ""
        filename = None
        for hline in headers_raw.split(b"\r\n"):
            hline_str = hline.decode("utf-8", errors="replace")
            if hline_str.lower().startswith("content-disposition"):
                content_disposition = hline_str

        m = re.search(r'name="([^"]*)"', content_disposition)
        if m:
            name = m.group(1)
        if name:
            if name not in result:
                result[name] = []
            result[name].append(data)

    return result


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class MockRagHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        print(f"[mock-rag] {self.address_string()} {fmt % args}", flush=True)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw) if raw.strip() else {}

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_ingest(self):
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if "multipart/form-data" not in ctype:
            self._send_json({"error": "multipart/form-data required"}, 400)
            return

        form = _parse_multipart(ctype, body)
        raw_files = form.get("file", [])
        if not raw_files:
            self._send_json({"error": "At least one file is required"}, 422)
            return

        tier_bytes = form.get("tier", [b"slow"])[0]
        strategy_bytes = form.get("strategy", [None])[0]
        hypothetical_bytes = form.get("hypothetical_questions", [None])[0]

        tier = tier_bytes.decode() if isinstance(tier_bytes, bytes) else "slow"
        strategy = strategy_bytes.decode() if isinstance(strategy_bytes, bytes) else None
        hypothetical = hypothetical_bytes.decode() if isinstance(hypothetical_bytes, bytes) else None

        job_id = _make_job_id()
        job_dir = UPLOAD_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        filenames = []

        for i, data in enumerate(raw_files):
            if isinstance(data, bytes):
                filename = f"upload_{i}"
                (job_dir / filename).write_bytes(data)
                filenames.append(filename)

        payload = {"path": str(job_dir), "tier": tier}
        if strategy:
            payload["strategy"] = strategy
        if hypothetical:
            payload["hypothetical_questions"] = hypothetical

        job = {
            "id": job_id, "kind": "ingest", "status": "queued",
            "filenames": filenames, "payload": payload,
            "result": None, "error": None,
            "created_at": _now(), "started_at": None, "completed_at": None,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job

        def _complete():
            time.sleep(0.5)
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "running"
                JOBS[job_id]["started_at"] = _now()
            time.sleep(0.5)
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "succeeded"
                JOBS[job_id]["completed_at"] = _now()
                JOBS[job_id]["result"] = {
                    "tier": tier,
                    "files_processed": len(filenames),
                    "files_failed": 0,
                    "files_skipped_duplicate": 0,
                    "chunks_created": 10,
                    "embeddings_indexed": 10,
                    "errors": [],
                    "source_paths": [str(job_dir / f) for f in filenames],
                }

        threading.Thread(target=_complete, daemon=True).start()
        self._send_json(_public_job(job), 202)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("/health", "/health/live"):
            self._send_json({"status": "ok"})
        elif path == "/health/ready":
            self._send_json({
                "status": "ready",
                "worker_running": True,
                "queue": {"queued": 0, "running": 0, "succeeded": 1, "failed": 0, "cancelled": 0},
                "collections": len(COLLECTIONS),
            })
        elif path == "/v1/ingestions":
            with JOBS_LOCK:
                jobs_list = list(JOBS.values())
            self._send_json({"jobs": [_public_job(j) for j in jobs_list], "total": len(jobs_list)})
        elif path.startswith("/v1/ingestions/"):
            job_id = path[len("/v1/ingestions/"):]
            job = _find_job(job_id)
            if job is None:
                self._send_json({"error": f"Ingestion job {job_id!r} not found"}, 404)
            else:
                self._send_json(_public_job(job))
        elif path == "/v1/collections":
            self._send_json(COLLECTIONS)
        elif path == "/":
            self.send_response(302)
            self.send_header("Location", "/docs")
            self.end_headers()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/v1/ingest":
            self._handle_ingest()
        elif path == "/v1/promote":
            body = self._read_body()
            job_id = _make_job_id()
            payload = {
                "source_path": body.get("source_path", ""),
                "to_tier": body.get("to_tier", "global"),
                "delete_old_chunks": body.get("delete_old_chunks", True),
            }
            job = {
                "id": job_id, "kind": "promote", "status": "queued",
                "filenames": [Path(payload["source_path"]).name],
                "payload": payload,
                "result": None, "error": None,
                "created_at": _now(), "started_at": None, "completed_at": None,
            }
            with JOBS_LOCK:
                JOBS[job_id] = job

            def _complete():
                time.sleep(0.3)
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "running"
                    JOBS[job_id]["started_at"] = _now()
                time.sleep(0.3)
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "succeeded"
                    JOBS[job_id]["completed_at"] = _now()
                    JOBS[job_id]["result"] = {
                        "tier": payload["to_tier"],
                        "files_processed": 1,
                        "chunks_created": 10,
                        "embeddings_indexed": 10,
                        "deleted_text_chunks": 5,
                        "deleted_image_chunks": 2,
                    }

            threading.Thread(target=_complete, daemon=True).start()
            self._send_json(_public_job(job), 202)

        elif path == "/v1/search":
            body = self._read_body()
            query = body.get("query", "")
            top_k = body.get("top_k", 5)
            mode = body.get("mode", "hybrid")
            results = []
            for i in range(min(top_k, 3)):
                results.append({
                    "rank": i + 1,
                    "score": round(0.95 - i * 0.1, 2),
                    "method": "reranked" if mode == "hybrid" else mode,
                    "source": "/app/data/ingestion/uploads/mock-job/sample.txt",
                    "chunk_type": "child",
                    "text": f"Mock result {i+1} for: {query[:50]}...",
                    "has_image": False,
                    "metadata": {"filename": "sample.txt", "ingestion_tier": "slow"},
                })
            self._send_json({"query": query, "results": results, "total": len(results)})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/v1/ingestions/"):
            job_id = path[len("/v1/ingestions/"):]
            job = _find_job(job_id)
            if job is None:
                self._send_json({"error": f"Job {job_id!r} not found"}, 404)
            elif job["status"] != "queued":
                self._send_json({"error": f"Cannot cancel {job['status']} job"}, 409)
            else:
                with JOBS_LOCK:
                    JOBS[job_id]["status"] = "cancelled"
                self._send_json(_public_job(JOBS[job_id]))
        elif path.startswith("/v1/collections/"):
            name = path[len("/v1/collections/"):]
            if name in COLLECTIONS:
                COLLECTIONS.remove(name)
            self._send_json({"dropped": name})
        else:
            self._send_json({"error": "not found"}, 404)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mock RAG Pipeline API server")
    parser.add_argument("--port", type=int, default=8093, help="Port to listen on")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), MockRagHandler)
    print(f"[mock-rag] Mock RAG API listening on http://localhost:{args.port}", flush=True)
    print(f"[mock-rag]   POST /v1/ingest   (multipart upload)", flush=True)
    print(f"[mock-rag]   GET  /v1/ingestions", flush=True)
    print(f"[mock-rag]   GET  /v1/ingestions/{{id}}", flush=True)
    print(f"[mock-rag]   DELETE /v1/ingestions/{{id}}", flush=True)
    print(f"[mock-rag]   POST /v1/promote", flush=True)
    print(f"[mock-rag]   POST /v1/search", flush=True)
    print(f"[mock-rag]   GET  /health, /health/live, /health/ready", flush=True)
    print(f"[mock-rag]   GET  /v1/collections", flush=True)
    print(f"[mock-rag]   DELETE /v1/collections/{{name}}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock-rag] Shutting down...", flush=True)
        server.server_close()


if __name__ == "__main__":
    main()
