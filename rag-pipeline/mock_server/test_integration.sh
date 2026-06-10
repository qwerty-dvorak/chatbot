#!/bin/bash
# End-to-end integration test of the RAG pipeline using the mock model server
# (stdlib-only, no real GPUs or model downloads needed).
#
# Starts:
#   1. Mock model server (chat, text embed, multimodal, reranker, OCR on 9000-9004)
#   2. Mock RAG API server (all API endpoints on :8093)
#   3. Milvus standalone (etcd + minio + milvus)
#
# Then runs ingest + search tests against the mock RAG API.
#
# Usage: bash mock_server/test_integration.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MOCK_RAG_DIR="$RAG_DIR/mock_rag"
ENV_FILE="$SCRIPT_DIR/.env"
SAMPLE_DOCUMENT="$RAG_DIR/data/sample_data/mock-integration.txt"
API_IMAGE="rag-api-mock-test"
CONTAINER_NAME="rag-api-mock-test"
MOCK_CONTAINER="rag-mock-server"
MOCK_RAG_CONTAINER="rag-mock-api"
API_PORT=${API_PORT:-8093}
API_URL="http://localhost:$API_PORT"
TEST_DATA_DIR=$(mktemp -d)

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

cleanup_all() {
  echo ""
  echo "Cleaning up..."
  docker rm -f "$CONTAINER_NAME" "$MOCK_CONTAINER" "$MOCK_RAG_CONTAINER" \
    test-milvus test-minio test-etcd 2>/dev/null || true
  rm -rf "$TEST_DATA_DIR"
}
trap cleanup_all EXIT

# ── 1. Ensure sample document exists ────────────────────────────────────────
if [[ ! -f "$SAMPLE_DOCUMENT" ]]; then
  echo "ERROR: sample document not found at $SAMPLE_DOCUMENT"
  exit 1
fi

# ── 2. Start mock model server ──────────────────────────────────────────────
echo "=== Starting mock model server ==="
bash "$SCRIPT_DIR/start.sh" --build

# ── 3. Start mock RAG API server ────────────────────────────────────────────
echo ""
echo "=== Starting mock RAG API server ==="
bash "$MOCK_RAG_DIR/start.sh" --build

# ── 4. Start local Milvus ───────────────────────────────────────────────────
echo ""
echo "=== Starting local Milvus ==="
bash "$RAG_DIR/runpod_start_local_milvus.sh"

# ── 5. Wait for all services ─────────────────────────────────────────────────
echo -n "Waiting for mock RAG API"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null || echo "000")
  [[ "$code" == "200" ]] && { echo " ready"; break; }
  sleep 2; echo -n "."
  [[ $i -eq 30 ]] && { echo " TIMEOUT"; exit 1; }
done

# ── 6. Run tests ────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Running end-to-end mock tests"
echo "══════════════════════════════════════════════════"

# Test 1: Health
echo "[1] GET /health"
resp=$(curl -sf "$API_URL/health" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "health → status=ok" || result FAIL "health → unexpected: $resp"
} || result FAIL "health → HTTP error"

# Test 2: Collections before ingest
echo "[2] GET /v1/collections (pre-ingest)"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && \
  result PASS "collections endpoint reachable" || result FAIL "collections → HTTP error: $resp"

# Test 3: Queue and await ingestion
echo "[3] POST /v1/ingest — $SAMPLE_DOCUMENT"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$SAMPLE_DOCUMENT" \
  -F "tier=instant" \
  -F "strategy=recursive" 2>&1) && {
  job_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  queued_status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  if [[ -n "$job_id" && "$queued_status" == "queued" ]]; then
    # Poll for completion
    for _ in $(seq 1 30); do
      JOB_RESPONSE=$(curl -sf "$API_URL/v1/ingestions/$job_id" 2>/dev/null || echo "")
      job_status=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
      [[ "$job_status" == "succeeded" ]] && break
      [[ "$job_status" == "failed" ]] && { result FAIL "ingest job → failed"; break 2; }
      sleep 0.5
    done
    chunks=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('result') or {}).get('chunks_created', 0))" 2>/dev/null || echo "0")
    [[ "$chunks" -gt 0 ]] && result PASS "ingest job → succeeded, chunks=$chunks" \
      || result FAIL "ingest job → no chunks: $JOB_RESPONSE"
  else
    result FAIL "ingest job → failed or timed out: ${JOB_RESPONSE:-$resp}"
  fi
} || result FAIL "ingest → HTTP error: $resp"

# Test 4: Job listing
echo "[4] GET /v1/ingestions"
resp=$(curl -sf "$API_URL/v1/ingestions?limit=10" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "ingestion jobs → $count job(s)" \
    || result FAIL "ingestion jobs → empty response"
} || result FAIL "ingestion jobs → HTTP error"

# Test 5: Collections after ingest
echo "[5] GET /v1/collections (post-ingest)"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "collections → $count collection(s) found" \
    || result FAIL "collections → no collections after ingest"
} || result FAIL "collections → HTTP error"

# Test 6: Hybrid search
echo "[6] POST /v1/search — mode=hybrid"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"what is the main topic","top_k":3,"mode":"hybrid"}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "hybrid search → $count result(s)" \
    || result FAIL "hybrid search → 0 results: $resp"
} || result FAIL "hybrid search → HTTP error"

# Test 7: Vector-only search
echo "[7] POST /v1/search — mode=vector"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"key updates","top_k":5,"mode":"vector","use_reranker":false}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "vector search → $count result(s)" \
    || result FAIL "vector search → 0 results: $resp"
} || result FAIL "vector search → HTTP error"

# Test 8: BM25-only search
echo "[8] POST /v1/search — mode=bm25"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"highlights summary","top_k":5,"mode":"bm25","use_reranker":false}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "bm25 search → $count result(s)" \
    || result FAIL "bm25 search → 0 results: $resp"
} || result FAIL "bm25 search → HTTP error"

# Test 9: Promote endpoint
echo "[9] POST /v1/promote"
resp=$(curl -sf -X POST "$API_URL/v1/promote" \
  -H "Content-Type: application/json" \
  -d '{"source_path":"/tmp/test.pdf","to_tier":"global"}' 2>&1) && {
  kind=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('kind',''))" 2>/dev/null || echo "")
  [[ "$kind" == "promote" ]] && result PASS "promote → kind=promote" \
    || result FAIL "promote → unexpected: $resp"
} || result FAIL "promote → HTTP error"

# Test 10: Health ready
echo "[10] GET /health/ready"
resp=$(curl -sf "$API_URL/health/ready" 2>&1) && {
  ready=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$ready" == "ready" ]] && result PASS "health/ready → ready" || result FAIL "health/ready → $resp"
} || result FAIL "health/ready → HTTP error"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
  echo " All mock integration tests passed!"
else
  echo " Some tests failed — see output above."
  exit 1
fi
