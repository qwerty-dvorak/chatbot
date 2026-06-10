#!/bin/bash
# Test the mock RAG API endpoints.
# Starts mock_rag server and runs end-to-end tests against it.
#
# Usage: bash mock_rag/test.sh [--no-start]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MOCK_IMAGE="rag-mock-api"
CONTAINER_NAME="rag-mock-api"
API_PORT=${API_PORT:-8093}
API_URL="http://localhost:$API_PORT"
SAMPLE_FILE=$(mktemp)

echo "Mock RAG API test fixture" > "$SAMPLE_FILE"
trap "rm -f $SAMPLE_FILE; docker rm -f $CONTAINER_NAME 2>/dev/null || true" EXIT

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

# ── Start mock RAG API ──────────────────────────────────────────────────────
if [[ "${1:-}" != "--no-start" ]]; then
  echo "=== Starting mock RAG API ==="
  bash "$SCRIPT_DIR/start.sh" --build
  sleep 1
fi

# Wait for API
echo -n "Waiting for API"
for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null || echo "000")
  [[ "$code" == "200" ]] && { echo " ready"; break; }
  sleep 1; echo -n "."
  [[ $i -eq 20 ]] && { echo " TIMEOUT"; docker logs --tail=20 "$CONTAINER_NAME" 2>/dev/null || true; exit 1; }
done

echo ""
echo "══════════════════════════════════════════════════"
echo " Testing mock RAG API"
echo "══════════════════════════════════════════════════"

# Test 1: Health
echo "[1] GET /health"
resp=$(curl -sf "$API_URL/health" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "/health → ok" || result FAIL "/health → $resp"
} || result FAIL "/health → HTTP error"

# Test 2: Health live
echo "[2] GET /health/live"
resp=$(curl -sf "$API_URL/health/live" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "/health/live → ok" || result FAIL "/health/live → $resp"
} || result FAIL "/health/live → HTTP error"

# Test 3: Health ready
echo "[3] GET /health/ready"
resp=$(curl -sf "$API_URL/health/ready" 2>&1) && {
  ready=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$ready" == "ready" ]] && result PASS "/health/ready → ready" || result FAIL "/health/ready → $resp"
} || result FAIL "/health/ready → HTTP error"

# Test 4: Ingest
echo "[4] POST /v1/ingest"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$SAMPLE_FILE" \
  -F "tier=slow" 2>&1) && {
  job_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ -n "$job_id" && "$status" == "queued" ]] && result PASS "ingest → 202, queued, id=$job_id" \
    || result FAIL "ingest → unexpected response: $resp"
} || result FAIL "ingest → HTTP error: $resp"

# Test 5: Poll job until succeeded
echo "[5] GET /v1/ingestions/$job_id"
for _ in $(seq 1 30); do
  resp=$(curl -sf "$API_URL/v1/ingestions/$job_id" 2>/dev/null || echo "")
  j_status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$j_status" == "succeeded" ]] && { result PASS "job → succeeded"; break; }
  [[ "$j_status" == "failed" ]] && { result FAIL "job → failed: $resp"; break; }
  sleep 0.5
done

# Test 6: List ingestions
echo "[6] GET /v1/ingestions"
resp=$(curl -sf "$API_URL/v1/ingestions?limit=10" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "ingestions → $count job(s)" || result FAIL "ingestions → empty"
} || result FAIL "ingestions → HTTP error"

# Test 7: Collections
echo "[7] GET /v1/collections"
resp=$(curl -sf "$API_URL/v1/collections" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "collections → $count found" || result FAIL "collections → empty"
} || result FAIL "collections → HTTP error"

# Test 8: Search
echo "[8] POST /v1/search"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"test query","top_k":3,"mode":"hybrid"}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "search → $count result(s)" || result FAIL "search → empty: $resp"
} || result FAIL "search → HTTP error"

# Test 9: Promote
echo "[9] POST /v1/promote"
resp=$(curl -sf -X POST "$API_URL/v1/promote" \
  -H "Content-Type: application/json" \
  -d '{"source_path":"/tmp/doc.pdf","to_tier":"global"}' 2>&1) && {
  kind=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('kind',''))" 2>/dev/null || echo "")
  [[ "$kind" == "promote" ]] && result PASS "promote → kind=promote" || result FAIL "promote → unexpected: $resp"
} || result FAIL "promote → HTTP error"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]] && echo " All mock RAG API tests passed!" || { echo " Some tests failed."; exit 1; }
