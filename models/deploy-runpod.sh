#!/bin/bash
# Deploy all model pods on RunPod cloud GPUs (single unified script).
# Deprecates the per-service deploy scripts previously in chatbot-service/ and rag-pipeline/.
#
# Prerequisites:
#   export HF_TOKEN=hf_...
#
# Usage:
#   bash deploy-runpod.sh               # deploy all pods + wait for readiness
#   bash deploy-runpod.sh --no-wait     # deploy only
#   bash deploy-runpod.sh --clean       # teardown first then deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_state"
TS=$(date +%s)
GPU_ID="${GPU_ID:-NVIDIA GeForce RTX 4090}"
CLOUD_TYPE="${CLOUD_TYPE:-community}"
NO_WAIT=false
[[ "${1:-}" == "--no-wait" ]] && NO_WAIT=true

if [[ "$*" == *"--clean"* ]]; then
  bash "$SCRIPT_DIR/teardown-runpod.sh" 2>/dev/null || true
fi

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERROR: HF_TOKEN is required."
  echo "  export HF_TOKEN=hf_..."
  exit 1
fi

HF_ENV_JSON="{\"HF_TOKEN\":\"${HF_TOKEN}\",\"HUGGING_FACE_HUB_TOKEN\":\"${HF_TOKEN}\"}"
CREATED_TPLS=()
CREATED_PODS=()

cleanup_on_error() {
  echo ""; echo "Error — cleaning up..." >&2
  for pod_id in "${CREATED_PODS[@]}"; do runpodctl pod delete "$pod_id" 2>/dev/null || true; done
  for tpl_id in "${CREATED_TPLS[@]}"; do runpodctl template delete "$tpl_id" 2>/dev/null || true; done
}
trap cleanup_on_error ERR

create_template() {
  local name="$1"; shift
  local out; out=$(runpodctl template create --name "$name" "$@" 2>&1)
  local id; id=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null) \
    || { echo "  Template create failed: $out" >&2; exit 1; }
  CREATED_TPLS+=("$id"); echo "$id"
}

create_pod() {
  local name="$1"; shift
  local out; out=$(runpodctl pod create --name "$name" "$@" 2>&1)
  local id; id=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null) \
    || { echo "  Pod create failed: $out" >&2; exit 1; }
  CREATED_PODS+=("$id"); echo "$id"
}

# ═════════════════════════════════════════════════════════════════════════════
# 1. CHAT LLM — google/gemma-4-E4B-it (requires HF token for gated model)
# ═════════════════════════════════════════════════════════════════════════════
echo "Creating chat-llm template..."
CHAT_TPL=$(create_template "chat-llm-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd "-c,exec python3 -m vllm.entrypoints.openai.api_server --model google/gemma-4-E4B-it --trust-remote-code --port 8000 --gpu-memory-utilization 0.90 --max-model-len 32768 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 --chat-template-content-format auto" \
  --env "$HF_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)

echo "Creating chat-llm pod ($GPU_ID)..."
CHAT_POD=$(create_pod "chat-llm" \
  --template-id "$CHAT_TPL" \
  --gpu-id "$GPU_ID" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)

# ═════════════════════════════════════════════════════════════════════════════
# 2. TEXT EMBEDDING — nvidia/llama-embed-nemotron-8b
# ═════════════════════════════════════════════════════════════════════════════
echo "Creating text-embed template..."
TEXT_TPL=$(create_template "rag-text-embed-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd "-c,exec python3 -m vllm.entrypoints.openai.api_server --model nvidia/llama-embed-nemotron-8b --trust-remote-code --port 8000 --gpu-memory-utilization 0.90 --max-model-len 8192" \
  --env "$HF_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)

echo "Creating text-embed pod..."
TEXT_POD=$(create_pod "rag-text-embed" \
  --template-id "$TEXT_TPL" \
  --gpu-id "NVIDIA GeForce RTX 3090" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)

# ═════════════════════════════════════════════════════════════════════════════
# 3. MULTIMODAL EMBEDDING — nvidia/nemotron-colembed-vl-8b-v2
# ═════════════════════════════════════════════════════════════════════════════
MM_ENV_JSON=$(python3 -c "
import json
d = {'HF_TOKEN': '${HF_TOKEN}', 'HUGGING_FACE_HUB_TOKEN': '${HF_TOKEN}', 'MM_LIMIT': json.dumps({'image': 1, 'video': 0})}
print(json.dumps(d))")

echo "Creating multimodal-embed template..."
MM_TPL=$(create_template "rag-mm-embed-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd '-c,exec python3 -m vllm.entrypoints.openai.api_server --model nvidia/nemotron-colembed-vl-8b-v2 --trust-remote-code --runner pooling --port 8000 --gpu-memory-utilization 0.90 --max-model-len 8192 --limit-mm-per-prompt "$MM_LIMIT" --skip-mm-profiling' \
  --env "$MM_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)

echo "Creating multimodal-embed pod..."
MM_POD=$(create_pod "rag-mm-embed" \
  --template-id "$MM_TPL" \
  --gpu-id "NVIDIA GeForce RTX 3090" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)

# ═════════════════════════════════════════════════════════════════════════════
# 4. RERANKER — Qwen/Qwen3-VL-Reranker-8B
# ═════════════════════════════════════════════════════════════════════════════
RERANKER_ENV_JSON=$(python3 -c "
import json
d = {'HF_TOKEN': '${HF_TOKEN}', 'HUGGING_FACE_HUB_TOKEN': '${HF_TOKEN}', 'HF_OVERRIDES': json.dumps({'architectures': ['Qwen3VLForSequenceClassification'], 'classifier_from_token': ['no', 'yes'], 'is_original_qwen3_reranker': True})}
print(json.dumps(d))")

echo "Creating reranker template..."
RERANKER_TPL=$(create_template "rag-reranker-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd '-c,exec python3 -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-VL-Reranker-8B --runner pooling --trust-remote-code --port 8000 --gpu-memory-utilization 0.90 --max-model-len 4096 --hf-overrides "$HF_OVERRIDES"' \
  --env "$RERANKER_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)

echo "Creating reranker pod..."
RERANKER_POD=$(create_pod "rag-reranker" \
  --template-id "$RERANKER_TPL" \
  --gpu-id "NVIDIA GeForce RTX 3090" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)

# ── Persist state ────────────────────────────────────────────────────────────
cat > "$STATE_FILE" <<EOF
CHAT_POD=$CHAT_POD
CHAT_TPL=$CHAT_TPL
TEXT_POD=$TEXT_POD
TEXT_TPL=$TEXT_TPL
MM_POD=$MM_POD
MM_TPL=$MM_TPL
RERANKER_POD=$RERANKER_POD
RERANKER_TPL=$RERANKER_TPL
EOF

trap - ERR

echo ""
echo "All pods deployed. IDs saved to $STATE_FILE"
echo "  chat-llm       ($GPU_ID)        $CHAT_POD"
echo "  text-embed     (RTX 3090)       $TEXT_POD"
echo "  mm-embed       (RTX 3090)       $MM_POD"
echo "  reranker       (RTX 3090)       $RERANKER_POD"

if $NO_WAIT; then
  echo ""
  echo "Run 'bash models/wait-runpod.sh' once pods show RUNNING."
  exit 0
fi

# ── Wait for all pods + detect URLs ─────────────────────────────────────────
wait_running() {
  local name="$1" pod_id="$2" max="${3:-300}"
  local elapsed=0
  echo -n "  $name ($pod_id) RUNNING"
  while true; do
    local s; s=$(runpodctl pod get "$pod_id" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('desiredStatus') or d.get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    [[ "$s" == "RUNNING" ]] && { echo " ✓"; return 0; }
    [[ "$s" == "EXITED" || "$s" == "FAILED" ]] && { echo " ✗ (status=$s)"; return 1; }
    sleep 15; elapsed=$((elapsed + 15)); echo -n "."
    [[ $elapsed -ge $max ]] && { echo " TIMEOUT"; return 1; }
  done
}

wait_http() {
  local name="$1" url="$2" max="${3:-1800}"
  local elapsed=0
  echo -n "  $name healthy"
  while true; do
    local code; code=$(curl -sk -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    [[ "$code" == "200" ]] && { echo " ✓"; return 0; }
    sleep 30; elapsed=$((elapsed + 30)); echo -n "."
    [[ $elapsed -ge $max ]] && { echo " TIMEOUT (last HTTP $code)"; return 1; }
  done
}

echo ""
echo "Waiting for pods to reach RUNNING..."
wait_running "chat-llm"       "$CHAT_POD"
wait_running "text-embed"    "$TEXT_POD"
wait_running "mm-embed"      "$MM_POD"
wait_running "reranker"      "$RERANKER_POD"

CHAT_URL="https://${CHAT_POD}-8000.proxy.runpod.net"
TEXT_URL="https://${TEXT_POD}-8000.proxy.runpod.net"
MM_URL="https://${MM_POD}-8000.proxy.runpod.net"
RERANKER_URL="https://${RERANKER_POD}-8000.proxy.runpod.net"

echo ""
echo "Waiting for vLLM health endpoints..."
wait_http "chat-llm"    "$CHAT_URL/health"
wait_http "text-embed"  "$TEXT_URL/health"
wait_http "mm-embed"    "$MM_URL/health"
wait_http "reranker"    "$RERANKER_URL/health"

# Detect embedding dimensions
detect_dim() {
  local url="$1" model="$2" endpoint="${3:-/v1/embeddings}"
  python3 -c "
import urllib.request, json, sys
payload = json.dumps({'model': '$model', 'input': 'test'}).encode()
req = urllib.request.Request('$url$endpoint', data=payload, headers={'Content-Type': 'application/json'}, method='POST')
try:
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    item = resp['data'][0]
    emb = item.get('embedding') or item['data'][0]
    print(len(emb))
except: sys.exit(1)" 2>/dev/null || echo ""
}
TEXT_DIM=$(detect_dim "$TEXT_URL" "nvidia/llama-embed-nemotron-8b") || TEXT_DIM=4096
MM_DIM=$(detect_dim "$MM_URL" "nvidia/nemotron-colembed-vl-8b-v2" "/pooling") || MM_DIM=4096

# Write proxy URLs to state
cat >> "$STATE_FILE" <<EOF
CHAT_URL=${CHAT_URL}
TEXT_URL=${TEXT_URL}
MM_URL=${MM_URL}
RERANKER_URL=${RERANKER_URL}
TEXT_DIM=${TEXT_DIM}
MM_DIM=${MM_DIM}
EOF

echo ""
echo "══════════════════════════════════════════════════"
echo " All RunPod models ready!"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM        $CHAT_URL"
echo "  Text Embed      $TEXT_URL  (dim: $TEXT_DIM)"
echo "  MM Embed        $MM_URL    (dim: $MM_DIM)"
echo "  Reranker        $RERANKER_URL"
