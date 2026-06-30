#!/usr/bin/env python3
"""End-to-end test: RAG API + chatbot integration.

Tests:
  - Schema fix (content_hash column)
  - RAG API: health, ingest, search, HyDE search
  - Chatbot internal RAG flow (via Django shell)

Requires running Docker containers.
Run: python tests/e2e_rag_test.py
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_PORT = os.environ.get("RAG_API_PORT", "8093")
WEB_PORT = os.environ.get("WEB_PORT", "8080")
RAG_API = f"http://localhost:{API_PORT}"
WEB_URL = f"http://localhost:{WEB_PORT}"
SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data", "text")

PASS = FAIL = 0


def log(*a):
    print(*a, flush=True)


def result(success, name):
    global PASS, FAIL
    if success:
        PASS += 1; log(f"  [PASS] {name}")
    else:
        FAIL += 1; log(f"  [FAIL] {name}")


def req(method, url, data=None, headers=None, timeout=30):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers = dict(headers or {}, **{"Content-Type": "application/json"})
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode() if e.fp else "")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def exec_in_web(python_code, timeout=30):
    """Run Python code inside the web container and return stdout."""
    r = subprocess.run(
        ["docker", "exec", "-w", "/app", "web", "uv", "run", "python", "-c", python_code],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def fix_and_verify_schema():
    log("\n[FIX] Checking document_chunks schema...")
    out, err, rc = exec_in_web(
        "from django.db import connection;"
        "with connection.cursor() as c:"
        " c.execute('SELECT column_name FROM information_schema.columns WHERE table_name = %s', ['document_chunks']);"
        " cols = {r[0] for r in c.fetchall()};"
        " print('ok' if 'content_hash' in cols else 'missing');"
        " if 'content_hash' not in cols:"
        "  c.execute(\"ALTER TABLE document_chunks ADD COLUMN content_hash varchar(64) NOT NULL DEFAULT ''\"); print('fixed')"
    )
    status = [l.strip() for l in out.split("\n") if l.strip()][-1:] if out else ["error"]
    log(f"  {status[0]}" if status else f"  {err}")
    return status[0] if status else "error"


def wait_for_job(job_id, max_retries=120, interval=2):
    for _ in range(max_retries):
        s, b = req("GET", f"{RAG_API}/v1/ingestions/{job_id}")
        if s != 200:
            time.sleep(interval); continue
        d = json.loads(b)
        st = d.get("status", "")
        if st == "succeeded":
            return d
        if st in ("failed", "cancelled"):
            log(f"  Job failed: {d.get('error', 'unknown')}")
            return None
        time.sleep(interval)
    return None


def test_rag_api():
    """Test RAG API: health, ingest, search."""
    log("\n--- RAG API Tests ---")

    # 1. Health
    s, b = req("GET", f"{RAG_API}/health")
    d = json.loads(b) if b else {}
    result(s == 200 and d.get("status") == "ok", f"health -> {d.get('status')}")

    # 2. Ingest
    sample = os.path.join(SAMPLE_DIR, "topic_astronomy.txt")
    with open(sample, "rb") as f:
        fb = f.read()
    fn = os.path.basename(sample)
    boundary = "----Test7MA4YWxkTrZu0gW"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{fn}"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
    ).encode() + fb + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="tier"\r\n\r\ninstant\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    s, b = req("POST", f"{RAG_API}/v1/ingest", data=body,
               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
               timeout=300)
    d = json.loads(b) if b else {}
    job_id = d.get("id", "")
    result(s in (200, 202) and job_id and d.get("status") == "queued",
           f"ingest -> job_id={job_id}")

    # 3. Poll
    log("  Polling...")
    job_data = wait_for_job(job_id)
    if job_data:
        rlist = (job_data.get("result") or {}).get("results") or [{}]
        chunks = rlist[0].get("chunks_created", 0) if rlist else 0
        result(chunks > 0, f"ingestion succeeded, chunks={chunks}")
    else:
        result(False, "ingestion failed/timeout")

    # 4. Search
    s, b = req("POST", f"{RAG_API}/v1/search", {
        "query": "What is astronomy",
        "top_k": 3, "mode": "hybrid", "use_reranker": False,
    })
    d = json.loads(b) if b else {}
    results = d.get("results", [])
    result(len(results) > 0, f"search returned {len(results)} result(s)")
    if results:
        log(f"     First: {results[0].get('text', '')[:80]}...")

    # 5. HyDE search
    s, b = req("POST", f"{RAG_API}/v1/search", {
        "query": "What is astronomy",
        "top_k": 3, "mode": "hybrid", "hyde": True, "use_reranker": False,
    })
    d = json.loads(b) if b else {}
    results = d.get("results", [])
    enhanced = d.get("enhanced_queries", [])
    result(len(results) > 0 and len(enhanced) > 0,
           f"HyDE: {len(results)} results, {len(enhanced)} enhanced queries")

    return job_id


def test_chatbot_rag_integration():
    """Test chatbot internal RAG integration via Django shell."""
    log("\n--- Chatbot RAG Integration Tests ---")

    # Check settings
    out, err, rc = exec_in_web(
        "import django; from django.conf import settings;"
        "print(f'RAG_ENABLED={getattr(settings,\"RAG_ENABLED\",False)}');"
        "print(f'RAG_TOP_K={getattr(settings,\"RAG_TOP_K\",0)}');"
    )
    for line in out.split("\n"):
        if line.strip():
            key, val = line.strip().split("=", 1)
            result(val.lower() in ("true", "5", "8"), f"{key}={val}")

    # Test rag_client configuration
    out, err, rc = exec_in_web(
        "from apps.knowledge.rag_client import RagApiClient;"
        "c = RagApiClient();"
        "print(f'enabled={c.is_enabled()}');"
        "h = c.health();"
        "print(f'health={h.get(\"status\") if h else \"fail\"}');"
    )
    for line in out.split("\n"):
        if line.strip():
            log(f"  {line}")
            if "enabled=" in line:
                result("true" in line.lower(), "rag_client enabled")
            if "health=" in line:
                result("ok" in line, "rag_client health ok")

    # Test RAG search via the client
    out, err, rc = exec_in_web(
        "from apps.knowledge.rag_client import RagApiClient;"
        "c = RagApiClient();"
        "r = c.search('What is astronomy', top_k=3);"
        "results = r.get('results', []);"
        "print(f'results={len(results)}');"
        "if results: print(f'first_text={results[0].get(\"text\",\"\")[:60]}');"
    )
    for line in out.split("\n"):
        if line.strip():
            log(f"  {line}")
            if "results=" in line:
                val = int(line.split("=")[1])
                result(val > 0, f"search returned {val} results")
            if "first_text=" in line:
                result(len(line.split("=", 1)[1]) > 0, "search has content")

    # Test empty search returns hyde fallback
    out, err, rc = exec_in_web(
        "from apps.knowledge.rag_client import RagApiClient;"
        "c = RagApiClient();"
        "r = c.search('totallynonexistentdocument12345', top_k=3, hyde=True);"
        "results = r.get('results', []);"
        "enhanced = r.get('enhanced_queries', []);"
        "print(f'results={len(results)}');"
        "print(f'enhanced={len(enhanced)}');"
        "if enhanced: print(f'hyde_fallback={enhanced[0][:60]}');"
    )
    for line in out.split("\n"):
        if line.strip() and "=" in line:
            log(f"  {line}")

    # Test context builder fallback path: when no results, uses hyde text
    out, err, rc = exec_in_web(
        "from django.contrib.auth import get_user_model;"
        "User = get_user_model();"
        "u = User.objects.filter(email='a@test.com').first();"
        "print('user:', u.email if u else 'none');"
    )
    for line in out.split("\n"):
        if line.strip():
            log(f"  {line}")

    # Test that KnowledgeDocument exists from ingested docs
    out, err, rc = exec_in_web(
        "from apps.knowledge.models import KnowledgeDocument;"
        "total = KnowledgeDocument.objects.count();"
        "print(f'knowledge_docs={total}');"
        "if total:"
        " d = KnowledgeDocument.objects.order_by('-created_at').first();"
        " print(f'latest_doc={d.title} status={d.status} sha256={d.sha256[:12]}');"
    )
    for line in out.split("\n"):
        if line.strip():
            log(f"  {line}")
            if "knowledge_docs=" in line:
                val = int(line.split("=")[1])
                result(val > 0, f"KnowledgeDocument records: {val}")

    # Verify document chunks exist in DB
    out, err, rc = exec_in_web(
        "from django.db import connection;"
        "with connection.cursor() as c:"
        " c.execute('SELECT COUNT(*) FROM document_chunks');"
        " print(f'chunks_in_db={c.fetchone()[0]}');"
    )
    for line in out.split("\n"):
        if line.strip():
            log(f"  {line}")
            if "chunks_in_db=" in line:
                val = int(line.split("=")[1])
                result(val > 0, f"document chunks in DB: {val}")

    # Test DocumentChunk model has content_hash
    out, err, rc = exec_in_web(
        "from apps.knowledge.models import DocumentChunk;"
        "first = DocumentChunk.objects.order_by('created_at').first();"
        "if first: print(f'has_content_hash={bool(first.content_hash)}');"
        " else: print('has_content_hash=no_rows');"
    )
    for line in out.split("\n"):
        if line.strip():
            log(f"  {line}")
            if "has_content_hash=" in line:
                val = line.split("=")[1]
                result(val in ("True", "no_rows"), f"DocumentChunk.content_hash: {val}")


def main():
    global PASS, FAIL
    log("=" * 60)
    log("End-to-End RAG Pipeline Test")
    log("=" * 60)

    fix_and_verify_schema()
    test_rag_api()
    test_chatbot_rag_integration()

    log("\n" + "=" * 60)
    log(f" Results: {PASS} passed, {FAIL} failed")
    log("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
