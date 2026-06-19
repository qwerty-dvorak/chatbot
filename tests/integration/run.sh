#!/bin/bash
# Integration test: full RAG pipeline end-to-end.
# Tests document upload → ingest → search flow through the RAG API,
# and verifies the chatbot knowledge web UI is reachable.
#
# Prerequisites: Full stack must be running (bash start.sh)
#
# Usage:
#   bash tests/integration/run.sh                    # auto-detect runpod/local
#   bash tests/integration/run.sh --mode runpod      # use RunPod endpoints
#   bash tests/integration/run.sh --mode local       # use local endpoints
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

API_PORT=8093
WEB_PORT=8080
API_URL="http://localhost:$API_PORT"
WEB_URL="http://localhost:$WEB_PORT"
SAMPLE_DIR="$ROOT_DIR/sample_data/text"

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

wait_for_job() {
  local job_id=$1
  for _ in $(seq 1 120); do
    resp=$(curl -sf "$API_URL/v1/ingestions/$job_id" 2>/dev/null || echo '{"status":"unknown"}')
    status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
    case "$status" in
      succeeded) return 0 ;;
      failed|cancelled) return 1 ;;
    esac
    sleep 2
  done
  return 1
}

echo "══════════════════════════════════════════════════"
echo " RAG Integration Tests"
echo "══════════════════════════════════════════════════"

# ── Test 1: RAG API health ──────────────────────────────────────────────────
echo "[1/8] GET /health"
resp=$(curl -sf "$API_URL/health" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "RAG API health → ok" || result FAIL "RAG API health → $resp"
} || result FAIL "RAG API unreachable at $API_URL"

# ── Test 2: Upload document with tier=global ────────────────────────────────
FILE="$SAMPLE_DIR/topic_astronomy.txt"
echo "[2/8] POST /v1/ingest — tier=global"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$FILE" \
  -F "tier=global" 2>&1) && {
  job_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  qstatus=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ -n "$job_id" && "$qstatus" == "queued" ]] && result PASS "global ingest queued (job=$job_id)" \
    || result FAIL "global ingest → unexpected: $resp"
} || result FAIL "global ingest → HTTP error: $resp"

# ── Test 3: Upload document with tier=instant ───────────────────────────────
FILE2="$SAMPLE_DIR/factual_test.txt"
echo "[3/8] POST /v1/ingest — tier=instant (private)"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$FILE2" \
  -F "tier=instant" 2>&1) && {
  job_id2=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  qstatus2=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ -n "$job_id2" && "$qstatus2" == "queued" ]] && result PASS "instant ingest queued (job=$job_id2)" \
    || result FAIL "instant ingest → unexpected: $resp"
} || result FAIL "instant ingest → HTTP error: $resp"

# ── Test 4: Upload document with tier=slow (shared) ─────────────────────────
FILE3="$SAMPLE_DIR/topic_biology.txt"
echo "[4/8] POST /v1/ingest — tier=slow (shared)"
resp=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$FILE3" \
  -F "tier=slow" 2>&1) && {
  job_id3=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  qstatus3=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ -n "$job_id3" && "$qstatus3" == "queued" ]] && result PASS "slow ingest queued (job=$job_id3)" \
    || result FAIL "slow ingest → unexpected: $resp"
} || result FAIL "slow ingest → HTTP error: $resp"

# ── Test 5: Poll first job for completion ───────────────────────────────────
echo "[5/8] Poll job $job_id"
JOB_RESPONSE=""
if wait_for_job "$job_id"; then
  JOB_RESPONSE=$(curl -sf "$API_URL/v1/ingestions/$job_id" 2>/dev/null || echo "{}")
  chunks=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('result') or {}; results=r.get('results') or [{}]; print(results[0].get('chunks_created', 0))" 2>/dev/null || echo "0")
  [[ "$chunks" -gt 0 ]] && result PASS "global ingest job succeeded (chunks=$chunks)" \
    || result FAIL "global ingest job → no chunks: $(echo "$JOB_RESPONSE" | head -c 200)"
else
  result FAIL "global ingest job timed out or failed"
fi

# ── Test 6: Search ──────────────────────────────────────────────────────────
echo "[6/8] POST /v1/search — hybrid query"
SEARCH_QUERY="What is astronomy"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"$SEARCH_QUERY\",\"top_k\":3,\"mode\":\"hybrid\"}" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "hybrid search returned $count result(s)" \
    || result FAIL "hybrid search → 0 results: $(echo "$resp" | head -c 200)"
} || result FAIL "hybrid search → HTTP error: $resp"

# ── Test 7: Job listing ────────────────────────────────────────────────────
echo "[7/8] GET /v1/ingestions"
resp=$(curl -sf "$API_URL/v1/ingestions?limit=10" 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('jobs',[])))" 2>/dev/null || echo "0")
  [[ "$count" -ge 3 ]] && result PASS "ingestion jobs listing → $count jobs" \
    || result FAIL "ingestion jobs → expected >=3, got $count"
} || result FAIL "ingestion jobs → HTTP error: $resp"

# ── Test 8: Web UI knowledge page ───────────────────────────────────────────
echo "[8/8] GET /knowledge/ (web UI)"
code=$(curl -s -o /dev/null -w "%{http_code}" "$WEB_URL/knowledge/" 2>/dev/null || echo "000")
# 302 = redirect to login (expected for unauthenticated), 200 = loaded
if [[ "$code" == "302" || "$code" == "200" ]]; then
  result PASS "knowledge web UI reachable (HTTP $code)"
else
  result FAIL "knowledge web UI unreachable (HTTP $code)"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]] && echo " All integration tests passed!" || { echo " Some tests failed — see output above."; exit 1; }
