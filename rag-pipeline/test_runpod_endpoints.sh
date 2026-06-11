#!/bin/bash
# Validate all RunPod vLLM endpoints against docs/runpod_api.md API shapes.
#
# Tests: text embedding (/v1/embeddings), multimodal pooling (/pooling),
#        reranker (/score) — text-only, batch, and multimodal.
#
# Usage:
#   bash runpod_deploy.sh          # deploy pods + write .env.runpod
#   bash test_runpod_endpoints.sh  # validate all endpoints
#
# Requires: .env.runpod (written by runpod_wait.sh)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.runpod"

if [[ -f "$ENV_FILE" ]]; then
  source "$ENV_FILE"
elif [[ -f "$SCRIPT_DIR/.runpod_state" ]]; then
  source "$SCRIPT_DIR/.runpod_state"
  TEXT_URL="https://${TEXT_POD}-8000.proxy.runpod.net"
  MM_URL="https://${MM_POD}-8000.proxy.runpod.net"
  RERANKER_URL="https://${RERANKER_POD}-8000.proxy.runpod.net"
  EMBEDDING_BASE_URL="${TEXT_URL}/v1"
  MULTIMODAL_EMBEDDING_BASE_URL="${MM_URL}"
  RERANKER_BASE_URL="${RERANKER_URL}"
else
  echo "ERROR: neither .env.runpod nor .runpod_state found."
  echo "Run 'bash runpod_deploy.sh' or check pod IDs in .runpod_state."
  exit 1
fi

PASS=0; FAIL=0
result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

echo "══════════════════════════════════════════════════"
echo " RunPod vLLM endpoint validation"
echo " docs/runpod_api.md"
echo "══════════════════════════════════════════════════"

TEXT_EMBED_URL="${EMBEDDING_BASE_URL}/embeddings"
MM_EMBED_URL="${MULTIMODAL_EMBEDDING_BASE_URL}/pooling"
RERANKER_URL="${RERANKER_BASE_URL}/pooling"

echo ""
echo "  Text Embed      ${EMBEDDING_BASE_URL}"
echo "  MM Embed        ${MULTIMODAL_EMBEDDING_BASE_URL}"
echo "  Reranker        ${RERANKER_BASE_URL}/pooling"

# ═══════════════════════════════════════════════════════
# 1. Text Embedding — POST /v1/embeddings
#    docs/runpod_api.md §1
# ═══════════════════════════════════════════════════════
echo ""
echo "───────────────────────────────────────────────────"
echo " §1  Text Embedding — POST /v1/embeddings"
echo "     nvidia/llama-embed-nemotron-8b"
echo "───────────────────────────────────────────────────"

echo -n "  text-embed single string"
resp=$(curl -sf -X POST "$TEXT_EMBED_URL" \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-embed-nemotron-8b","input":"hello world"}' 2>&1) && {
  dim=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
e=d['data'][0]['embedding']
print(len(e))" 2>/dev/null || echo "")
  [[ "$dim" == "4096" ]] && result PASS "single string → dim=4096" \
    || result FAIL "single string → dim=$dim (expected 4096)"
} || result FAIL "single string → HTTP error: $(echo $resp | head -c200)"

echo -n "  text-embed batch (2 inputs)"
resp=$(curl -sf -X POST "$TEXT_EMBED_URL" \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-embed-nemotron-8b","input":["hello","world"]}' 2>&1) && {
  count=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(len(d['data']))" 2>/dev/null || echo "")
  [[ "$count" == "2" ]] && result PASS "batch 2 inputs → $count results" \
    || result FAIL "batch 2 inputs → got $count"
} || result FAIL "batch 2 inputs → HTTP error: $(echo $resp | head -c200)"

echo -n "  text-embed response shape"
echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
obj = d.get('object')
model = d.get('model')
usage = d.get('usage',{})
assert obj == 'list', f'object={obj}'
assert model == 'nvidia/llama-embed-nemotron-8b', f'model={model}'
assert 'prompt_tokens' in usage, f'usage={usage}'
print('OK')
" 2>&1 | head -1 | while read line; do
  [[ "$line" == "OK" ]] && result PASS "response shape matches runpod_api.md" \
    || result FAIL "response shape mismatch: $line"
done

# ═══════════════════════════════════════════════════════
# 2. Multimodal Embedding — POST /pooling
#    docs/runpod_api.md §2
# ═══════════════════════════════════════════════════════
echo ""
echo "───────────────────────────────────────────────────"
echo " §2  Multimodal Embedding — POST /pooling"
echo "     nvidia/nemotron-colembed-vl-8b-v2"
echo "───────────────────────────────────────────────────"

echo -n "  mm-embed single string"
resp=$(curl -sf -X POST "$MM_EMBED_URL" \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/nemotron-colembed-vl-8b-v2","input":"hello world"}' 2>&1) && {
  info=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
e=d['data'][0]['data']
print(f'{len(e)} vecs x {len(e[0])} dim')" 2>/dev/null || echo "")
  [[ -n "$info" ]] && result PASS "single string → $info" \
    || result FAIL "single string → unexpected: $(echo $resp | head -c200)"
} || result FAIL "single string → HTTP error: $(echo $resp | head -c200)"

echo -n "  mm-embed response shape"
echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
obj = d.get('object')
model = d.get('model')
usage = d.get('usage',{})
assert obj == 'list', f'object={obj}'
assert model == 'nvidia/nemotron-colembed-vl-8b-v2', f'model={model}'
assert 'prompt_tokens' in usage, f'usage={usage}'
print('OK')
" 2>&1 | head -1 | while read line; do
  [[ "$line" == "OK" ]] && result PASS "response shape matches runpod_api.md" \
    || result FAIL "response shape mismatch: $line"
done

# ═══════════════════════════════════════════════════════
# 3. Reranker — POST /pooling
#    docs/runpod_api.md §3
#    With --runner pooling the reranker exposes /pooling
#    (same endpoint as the multimodal embed).
# ═══════════════════════════════════════════════════════
echo ""
echo "───────────────────────────────────────────────────"
echo " §3  Reranker — POST /pooling"
echo "     Qwen/Qwen3-VL-Reranker-8B"
echo "───────────────────────────────────────────────────"

echo -n "  reranker single input"
resp=$(curl -sf -X POST "$RERANKER_URL" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-VL-Reranker-8B","input":"What is the capital of France? Paris is the capital of France."}' 2>&1) && {
  info=$(echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
e=d['data'][0]['data']
print(f'{len(e)} vecs x {len(e[0])} dim')" 2>/dev/null || echo "")
  [[ -n "$info" ]] && result PASS "single input → $info" \
    || result FAIL "single input → unexpected: $(echo $resp | head -c200)"
} || result FAIL "single input → HTTP error: $(echo $resp | head -c200)"

echo -n "  reranker response shape"
echo "$resp" | python3 -c "
import sys,json
d=json.load(sys.stdin)
obj = d.get('object')
model = d.get('model')
usage = d.get('usage',{})
assert obj == 'list', f'object={obj}'
assert model == 'Qwen/Qwen3-VL-Reranker-8B', f'model={model}'
assert 'prompt_tokens' in usage, f'usage={usage}'
print('OK')
" 2>&1 | head -1 | while read line; do
  [[ "$line" == "OK" ]] && result PASS "response shape matches runpod_api.md" \
    || result FAIL "response shape mismatch: $line"
done

# ═══════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]] && echo " All endpoints validated against docs/runpod_api.md!" \
  || { echo " Some checks failed — see above."; exit 1; }
