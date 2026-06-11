#!/bin/bash
# End-to-end test of the RAG API against RunPod pods.
# Builds the rag-api Docker image locally, runs it pointed at RunPod services,
# uploads the sample PDF, then exercises all endpoints.
#
# Usage: bash test_api.sh
# Requires: .env.runpod (written by runpod_wait.sh)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.runpod"
SAMPLE_PDF="$SCRIPT_DIR/data/sample_data/2025-0910-newsletter.pdf"
API_IMAGE="rag-api-test"
CONTAINER_NAME="rag-api-test"
API_PORT=${API_PORT:-8093}
API_URL="http://localhost:$API_PORT"

[[ -f "$ENV_FILE" ]] || { echo "ERROR: .env.runpod not found. Run runpod_wait.sh first."; exit 1; }
[[ -f "$SAMPLE_PDF" ]] || { echo "ERROR: sample PDF not found at $SAMPLE_PDF"; exit 1; }

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

wait_for_job() {
  local job_id=$1
  for _ in $(seq 1 180); do
    JOB_RESPONSE=$(curl -sf "$API_URL/v1/ingestions/$job_id")
    job_status=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$job_status" in
      succeeded) return 0 ;;
      failed|cancelled) return 1 ;;
    esac
    sleep 1
  done
  return 1
}

# ── Build RAG API image ───────────────────────────────────────────────────────
echo "Building rag-api Docker image..."
docker build -t "$API_IMAGE" "$SCRIPT_DIR" --quiet
echo "  Built $API_IMAGE"

# ── Start RAG API container ───────────────────────────────────────────────────
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
echo "Starting rag-api container (env from .env.runpod)..."
# --network host: container shares host network stack so it can reach
# localhost services (mock server ports 9000-9004, Milvus port 19530).
# The API binds to :8093 on the host directly; -p flag not needed.
docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  --env-file "$ENV_FILE" \
  -v "$SCRIPT_DIR/data:/app/data" \
  "$API_IMAGE"

cleanup() { echo ""; echo "Stopping rag-api container..."; docker rm -f "$CONTAINER_NAME" 2>/dev/null || true; }
trap cleanup EXIT

# ── Wait for API to be ready ──────────────────────────────────────────────────
echo -n "Waiting for API health"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null || echo "000")
  [[ "$code" == "200" ]] && { echo " ready"; break; }
  sleep 3; echo -n "."
  [[ $i -eq 40 ]] && { echo " TIMEOUT"; docker logs --tail=30 "$CONTAINER_NAME"; exit 1; }
done

echo ""
echo "══════════════════════════════════════════════════"
echo " Running end-to-end tests"
echo "══════════════════════════════════════════════════"

# ── Test 1: Health ────────────────────────────────────────────────────────────
echo "[1/8] GET /health"
resp=$(curl -sf "$API_URL/health" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "health → status=ok" || result FAIL "health → unexpected: $resp"
} || result FAIL "health → HTTP error"

# ── Test 2: Collections before ingest ─────────────────────────────────────────
echo "[2/8] GET /v1/collections (pre-ingest)"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && \
  result PASS "collections endpoint reachable" || result FAIL "collections → HTTP error: $resp"

# ── Test 3: Queue and await ingestion ─────────────────────────────────────────
echo "[3/8] POST /v1/ingest — queueing $SAMPLE_PDF"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$SAMPLE_PDF" \
  -F "tier=instant" \
  -F "strategy=recursive" 2>&1) && {
  job_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  queued_status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  if [[ -n "$job_id" && "$queued_status" == "queued" ]] && wait_for_job "$job_id"; then
    chunks=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('result') or {}).get('chunks_created', 0))")
    [[ "$chunks" -gt 0 ]] && result PASS "ingest job → succeeded, chunks=$chunks" \
      || result FAIL "ingest job → no chunks: $JOB_RESPONSE"
  else
    result FAIL "ingest job → failed or timed out: ${JOB_RESPONSE:-$resp}"
  fi
} || result FAIL "ingest → HTTP error: $resp"

# ── Test 4: Job listing ───────────────────────────────────────────────────────
echo "[4/8] GET /v1/ingestions"
resp=$(curl -sf "$API_URL/v1/ingestions?limit=10" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "ingestion jobs → $count job(s)" \
    || result FAIL "ingestion jobs → empty response"
} || result FAIL "ingestion jobs → HTTP error: $resp"

# ── Test 5: Collections after ingest ─────────────────────────────────────────
echo "[5/8] GET /v1/collections (post-ingest)"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "collections → $count collection(s) found" \
    || result FAIL "collections → no collections after ingest"
} || result FAIL "collections → HTTP error: $resp"

# ── Test 6: Hybrid search ─────────────────────────────────────────────────────
echo "[6/8] POST /v1/search — mode=hybrid"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"what is the main topic of this newsletter","top_k":3,"mode":"hybrid","use_reranker":true}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "hybrid search → $count result(s)" \
    || result FAIL "hybrid search → 0 results: $resp"
} || result FAIL "hybrid search → HTTP error: $resp"

# ── Test 7: Vector-only search ─────────────────────────────────────────────────
echo "[7/8] POST /v1/search — mode=vector"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"key updates and announcements","top_k":5,"mode":"vector","use_reranker":false}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "vector search → $count result(s)" \
    || result FAIL "vector search → 0 results: $resp"
} || result FAIL "vector search → HTTP error: $resp"

# ── Test 8: BM25-only search ──────────────────────────────────────────────────
echo "[8/8] POST /v1/search — mode=bm25"
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
[[ $FAIL -eq 0 ]] && echo " All tests passed!" || { echo " Some tests failed — see output above."; exit 1; }
