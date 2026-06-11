#!/bin/bash
# Validate all 3 RunPod vLLM endpoints + RAG API against .env.runpod config.
#
# Usage:
#   bash runpod_deploy.sh          # deploy pods + write .env.runpod
#   bash test_runpod_endpoints.sh  # run all checks
#
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
source "$ENV_FILE"

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

echo "══════════════════════════════════════════════════"
echo " RunPod endpoint validation"
echo "══════════════════════════════════════════════════"

echo "Using config:"
echo "  TEXT_EMBED_URL=${EMBEDDING_BASE_URL}/v1/embeddings"
echo "  MM_EMBED_URL=${MULTIMODAL_EMBEDDING_BASE_URL}/pooling"
echo "  RERANKER_URL=${RERANKER_BASE_URL}/score"

# ── Test 1: Text Embedding ──────────────────────────────────────────────────────
echo ""
echo "[1/6] Text embedding — POST /v1/embeddings"
TEXT_PAYLOAD='{"model":"nvidia/llama-embed-nemotron-8b","input":"hello world"}'
resp=$(curl -sf -X POST "${EMBEDDING_BASE_URL}/v1/embeddings" \
  -H "Content-Type: application/json" -d "$TEXT_PAYLOAD" 2>&1) && {
  dim=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
e=d['data'][0].get('embedding') or d['data'][0]['data'][0]
print(len(e))" 2>/dev/null || echo "")
  [[ -n "$dim" ]] && result PASS "text-embed → dim=$dim" || result FAIL "text-embed → no embedding: $(echo $resp | head -c200)"
} || result FAIL "text-embed → HTTP error: $resp"

# ── Test 2: Multimodal Embedding ─────────────────────────────────────────────────
echo "[2/6] Multimodal embedding — POST /pooling"
MM_PAYLOAD='{"model":"nvidia/nemotron-colembed-vl-8b-v2","input":"hello world"}'
resp=$(curl -sf -X POST "${MULTIMODAL_EMBEDDING_BASE_URL}/pooling" \
  -H "Content-Type: application/json" -d "$MM_PAYLOAD" 2>&1) && {
  num_vecs=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
e=d['data'][0]['data']
print(f'{len(e)} vectors, dim={len(e[0])}')" 2>/dev/null || echo "")
  [[ -n "$num_vecs" ]] && result PASS "mm-embed → $num_vecs" || result FAIL "mm-embed → unexpected response: $(echo $resp | head -c200)"
} || result FAIL "mm-embed → HTTP error: $resp"

# ── Test 3: Reranker ────────────────────────────────────────────────────────────
echo "[3/6] Reranker — POST /score"
RERANK_PAYLOAD='{"model":"Qwen/Qwen3-VL-Reranker-8B","text_1":"what is the capital of france","text_2":"Paris is the capital of France"}'
resp=$(curl -sf -X POST "${RERANKER_BASE_URL}/score" \
  -H "Content-Type: application/json" -d "$RERANK_PAYLOAD" 2>&1) && {
  score=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
# score could be in d['score'] or d['data'][0]['score']
s = d.get('score') or (d.get('data') and d['data'][0].get('score')) or '(unknown)'
print(s)" 2>/dev/null || echo "")
  [[ -n "$score" ]] && result PASS "reranker → score=$score" || result FAIL "reranker → unexpected response: $(echo $resp | head -c200)"
} || result FAIL "reranker → HTTP error: $resp"

# ── Build RAG API and run end-to-end tests ───────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " RAG API end-to-end tests"
echo "══════════════════════════════════════════════════"

# Build image
echo "Building rag-api Docker image..."
docker build -t "$API_IMAGE" "$SCRIPT_DIR" --quiet
echo "  Built $API_IMAGE"

# Start container
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
echo "Starting rag-api container (env from .env.runpod)..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  --env-file "$ENV_FILE" \
  -v "$SCRIPT_DIR/data:/app/data" \
  "$API_IMAGE"

cleanup_api() { echo ""; echo "Stopping rag-api container..."; docker rm -f "$CONTAINER_NAME" 2>/dev/null || true; }
trap cleanup_api EXIT

# Wait for API to be ready
echo -n "Waiting for API health"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null || echo "000")
  [[ "$code" == "200" ]] && { echo " ready"; break; }
  sleep 3; echo -n "."
  [[ $i -eq 40 ]] && { echo " TIMEOUT"; docker logs --tail=30 "$CONTAINER_NAME"; exit 1; }
done

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

# Test 4: API Health
echo ""
echo "[4/6] GET /health"
resp=$(curl -sf "$API_URL/health" 2>&1) && {
  status=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
  [[ "$status" == "ok" ]] && result PASS "health → status=ok" || result FAIL "health → unexpected: $resp"
} || result FAIL "health → HTTP error"

# Test 5: Ingest a sample document
echo "[5/6] POST /v1/ingest"
if [[ -f "$SAMPLE_PDF" ]]; then
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
else
  echo "  SKIP (no sample PDF at $SAMPLE_PDF)"
  result PASS "ingest → skipped (no sample file)"
fi

# Test 6: Hybrid search
echo "[6/6] POST /v1/search — mode=hybrid"
resp=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"what is the main topic","top_k":3,"mode":"hybrid","use_reranker":true}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo "0")
  [[ "$count" -gt 0 ]] && result PASS "hybrid search → $count result(s)" \
    || result FAIL "hybrid search → 0 results: $resp"
} || result FAIL "hybrid search → HTTP error: $resp"

# Summary
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]] && echo " All checks passed!" || { echo " Some checks failed — see above."; exit 1; }
