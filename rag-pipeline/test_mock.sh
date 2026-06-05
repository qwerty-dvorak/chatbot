#!/bin/bash
# End-to-end integration test of the RAG pipeline using the mock server
# (stdlib-only, no real GPUs or model downloads needed).
#
# Starts:
#   1. Mock server (chat, embed, multimodal, reranker on ports 9000-9003)
#   2. Milvus standalone (etcd + minio + milvus)
#   3. rag-api (FastAPI) pointed at the mock endpoints
#
# Then runs ingest + search tests against it.
#
# Usage: bash test_mock.sh [--clean]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.mock"
SAMPLE_PDF="$SCRIPT_DIR/data/sample_data/2025-0910-newsletter.pdf"
API_IMAGE="rag-api-mock-test"
CONTAINER_NAME="rag-api-mock-test"
API_PORT=${API_PORT:-8093}
API_URL="http://localhost:$API_PORT"

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

cleanup_all() {
  echo ""
  echo "Cleaning up..."
  docker rm -f "$CONTAINER_NAME" rag-mock-server test-milvus test-minio test-etcd 2>/dev/null || true
}
trap cleanup_all EXIT

# ── 1. Ensure sample PDF exists ─────────────────────────────────────────────
if [[ ! -f "$SAMPLE_PDF" ]]; then
  echo "ERROR: sample PDF not found at $SAMPLE_PDF"
  exit 1
fi

# ── 2. Start mock server ────────────────────────────────────────────────────
echo "=== Starting mock server ==="
bash "$SCRIPT_DIR/start-mock-server.sh" --build

# ── 3. Start local Milvus ───────────────────────────────────────────────────
echo ""
echo "=== Starting local Milvus ==="
bash "$SCRIPT_DIR/runpod_start_local_milvus.sh"

# ── 4. Build rag-api image ──────────────────────────────────────────────────
echo ""
echo "=== Building rag-api image ==="
docker build -t "$API_IMAGE" "$SCRIPT_DIR" --quiet
echo "  Built $API_IMAGE"

# ── 5. Start rag-api container ──────────────────────────────────────────────
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
echo "Starting rag-api container (env from .env.mock)..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  --env-file "$ENV_FILE" \
  -v "$SCRIPT_DIR/data:/app/data" \
  "$API_IMAGE"

# ── 6. Wait for API to be ready ──────────────────────────────────────────────
echo -n "Waiting for API health"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null || echo "000")
  [[ "$code" == "200" ]] && { echo " ready"; break; }
  sleep 3; echo -n "."
  [[ $i -eq 40 ]] && { echo " TIMEOUT"; docker logs --tail=30 "$CONTAINER_NAME"; exit 1; }
done

# ── 7. Run tests ────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Running end-to-end mock tests"
echo "══════════════════════════════════════════════════"

# Test 1: Health
echo "[1/7] GET /health"
resp=$(curl -sf "$API_URL/health" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "health → status=ok" || result FAIL "health → unexpected: $resp"
} || result FAIL "health → HTTP error"

# Test 2: Collections before ingest
echo "[2/7] GET /v1/collections (pre-ingest)"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && \
  result PASS "collections endpoint reachable" || result FAIL "collections → HTTP error: $resp"

# Test 3: Ingest sample PDF
echo "[3/7] POST /v1/ingest — uploading $SAMPLE_PDF"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$SAMPLE_PDF" \
  -F "strategy=recursive" 2>&1) && {
  ingest_status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
  chunks=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('stats',{}); print(s.get('chunks_indexed', s.get('total_chunks', 0)))" 2>/dev/null || echo "0")
  if [[ "$ingest_status" == "ok" && "$chunks" -gt 0 ]]; then
    result PASS "ingest → status=ok, chunks=$chunks"
  elif [[ "$ingest_status" == "ok" ]]; then
    result PASS "ingest → status=ok (chunk count unknown)"
  else
    result FAIL "ingest → unexpected: $resp"
  fi
} || result FAIL "ingest → HTTP error: $resp"

# Test 4: Collections after ingest
echo "[4/7] GET /v1/collections (post-ingest)"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "collections → $count collection(s) found" \
    || result FAIL "collections → no collections after ingest"
} || result FAIL "collections → HTTP error: $resp"

# Test 5: Hybrid search
echo "[5/7] POST /v1/search — mode=hybrid"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"what is the main topic of this newsletter","top_k":3,"mode":"hybrid","use_reranker":true}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "hybrid search → $count result(s)" \
    || result FAIL "hybrid search → 0 results: $resp"
} || result FAIL "hybrid search → HTTP error: $resp"

# Test 6: Vector-only search
echo "[6/7] POST /v1/search — mode=vector"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"key updates and announcements","top_k":5,"mode":"vector","use_reranker":false}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "vector search → $count result(s)" \
    || result FAIL "vector search → 0 results: $resp"
} || result FAIL "vector search → HTTP error: $resp"

# Test 7: BM25-only search
echo "[7/7] POST /v1/search — mode=bm25"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"highlights summary","top_k":5,"mode":"bm25","use_reranker":false}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "bm25 search → $count result(s)" \
    || result FAIL "bm25 search → 0 results: $resp"
} || result FAIL "bm25 search → HTTP error: $resp"

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
