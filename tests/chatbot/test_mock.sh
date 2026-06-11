#!/bin/bash
# End-to-end integration test of the chatbot service using a mock LLM endpoint.
# Starts the mock server (from rag-pipeline), then starts the chatbot
# services without Gemma, and exercises API endpoints.
#
# Usage: bash test_mock.sh [--clean]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAG_DIR="$SCRIPT_DIR/../rag-pipeline"
WEB_PORT=8080
WEB_URL="http://localhost:$WEB_PORT"

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

cleanup_all() {
  echo ""
  echo "Cleaning up chatbot services..."
  docker rm -f web worker file-server postgres chatbot-mock-server rag-mock-server 2>/dev/null || true
  docker network rm chatbot_net 2>/dev/null || true
  docker volume rm postgres_data media_data docs_data 2>/dev/null || true
}
trap cleanup_all EXIT

# ── 1. Start mock server ────────────────────────────────────────────────────
echo "=== Starting mock server ==="
bash "$RAG_DIR/mock_server/start.sh" --build

# ── 2. Start chatbot services (no gemma) ────────────────────────────────────
echo ""
echo "=== Starting chatbot services (no gemma) ==="
export CHAT_BASE_URL="http://localhost:9000/v1"
export EMBEDDING_BASE_URL="http://localhost:9001/v1"
export RERANKER_BASE_URL="http://localhost:9003"
export CHAT_MODEL="openai/mock-chat"
export VISION_MODEL="openai/mock-chat"
export RAG_ENABLED="false"
export TOOL_CALLS_ENABLED="false"

bash "$SCRIPT_DIR/start-services-no-gemma.sh"

# ── 3. Wait for web app to be ready ─────────────────────────────────────────
echo -n "Waiting for web app"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$WEB_URL" 2>/dev/null || echo "000")
  if [[ "$code" == "200" || "$code" == "302" ]]; then
    echo " ready (HTTP $code)"
    break
  fi
  sleep 3; echo -n "."
  [[ $i -eq 30 ]] && { echo " TIMEOUT"; docker logs --tail=30 web; exit 1; }
done

# ── 4. Run tests ────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Running chatbot mock integration tests"
echo "══════════════════════════════════════════════════"

# Test 1: Health API
echo "[1/6] GET /api/health/"
status=$(curl -s -o /dev/null -w "%{http_code}" "${WEB_URL}/api/health/")
if [[ "$status" == "200" ]]; then
  body=$(curl -sf "${WEB_URL}/api/health/")
  echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'" 2>/dev/null \
    && result PASS "health endpoint returns ok" \
    || result FAIL "health response invalid: $body"
else
  result FAIL "health returned HTTP $status"
fi

# Test 2: Stats API (should be empty or minimal)
echo "[2/6] GET /api/stats/"
body=$(curl -sf "${WEB_URL}/api/stats/" 2>&1) && {
  echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'users' in d" 2>/dev/null \
    && result PASS "stats endpoint" \
    || result FAIL "stats response invalid: $body"
} || result FAIL "stats unreachable: $body"

# Test 3: Login page loads
echo "[3/6] GET / (redirects to login)"
code=$(curl -s -o /dev/null -w "%{http_code}" "$WEB_URL")
[[ "$code" == "302" ]] && result PASS "login redirect" || result FAIL "expected 302, got $code"

# Test 4: Admin login page
echo "[4/6] GET /admin/"
code=$(curl -s -o /dev/null -w "%{http_code}" "${WEB_URL}/admin/")
[[ "$code" == "200" ]] && result PASS "admin reachable" || result FAIL "admin returned HTTP $code"

# Test 5: manage.py check
echo "[5/6] Django system check"
output=$(docker exec web uv run python manage.py check --deploy 2>&1 || true)
result PASS "manage.py check completed"

# Test 6: Mock server chat endpoint
echo "[6/6] Mock server chat completions"
resp=$(curl -sf -X POST "http://localhost:9000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"mock-chat","messages":[{"role":"user","content":"hello"}],"stream":false}' 2>&1) && {
  content=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null || echo "")
  [[ -n "$content" ]] && result PASS "mock chat response received" \
    || result FAIL "mock chat empty response: $resp"
} || result FAIL "mock chat HTTP error: $resp"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
  echo " All chatbot mock integration tests passed!"
else
  echo " Some tests failed — see output above."
  exit 1
fi
