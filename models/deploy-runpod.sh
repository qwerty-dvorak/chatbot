#!/bin/bash
# Deploy all model pods on RunPod cloud GPUs (single unified script).
# Fully idempotent: detects already-running pods by prefix match
# and only creates missing ones.
#
# Prerequisites:
#   export HF_TOKEN=hf_...
#
# Usage:
#   bash deploy-runpod.sh                   # deploy missing pods + wait for readiness
#   bash deploy-runpod.sh --no-wait         # deploy missing pods only (no RUNNING wait, no health check)
#   bash deploy-runpod.sh --no-healthcheck  # deploy + wait for RUNNING, skip HTTP health check
#   bash deploy-runpod.sh --clean           # teardown first then deploy
#   bash deploy-runpod.sh --refresh-env     # just re-detect existing pods and rewrite .env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.runpod"
TS=$(date +%s)
GPU_ID="${GPU_ID:-NVIDIA GeForce RTX 4090}"
CLOUD_TYPE="${CLOUD_TYPE:-community}"
NO_WAIT=false
NO_HEALTHCHECK="${NO_HEALTHCHECK:-false}"
REFRESH_ENV=false
for arg in "$@"; do
  [[ "$arg" == "--no-wait" ]] && NO_WAIT=true
  [[ "$arg" == "--no-healthcheck" ]] && NO_HEALTHCHECK=true
done
[[ "$*" == *"--refresh-env"* ]] && REFRESH_ENV=true

if [[ "$*" == *"--clean"* ]]; then
  bash "$SCRIPT_DIR/teardown-runpod.sh" 2>/dev/null || true
fi

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERROR: HF_TOKEN is required."
  echo "  export HF_TOKEN=hf_..."
  exit 1
fi

# Fetch existing pods
EXISTING_PODS=$(runpodctl pod list -o json 2>/dev/null || echo "[]")

# Match an existing pod by name prefix (e.g. "chat-llm" matches "chat-llm-lora")
existing_pod_id() {
  local prefix="$1"
  echo "$EXISTING_PODS" | python3 -c "
import sys, json
try:
    for p in json.load(sys.stdin):
        name = p.get('name', '')
        if name.startswith('$prefix') or name == '$prefix':
            print(p['id'])
            break
except: pass
" 2>/dev/null
}

# Get ALL pod data as JSON keyed by name prefix
pod_data_by_prefix() {
  local prefix="$1"
  echo "$EXISTING_PODS" | python3 -c "
import sys, json
try:
    for p in json.load(sys.stdin):
        name = p.get('name', '')
        if name.startswith('$prefix') or name == '$prefix':
            print(json.dumps(p))
            break
except: pass
" 2>/dev/null
}

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
  local existing_id; existing_id=$(existing_pod_id "$name")
  if [[ -n "$existing_id" ]]; then
    echo "  Pod '$name' already exists (id=$existing_id), skipping." >&2
    echo "$existing_id"
    return
  fi
  local out; out=$(runpodctl pod create --name "$name" "$@" 2>&1)
  local id; id=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null) \
    || { echo "  Pod create failed: $out" >&2; exit 1; }
  CREATED_PODS+=("$id"); echo "$id"
}

# Define expected pod names and their GPU types
declare -A POD_NAMES=(
  ["chat-llm"]="chat-llm"
  ["rag-text-embed"]="rag-text-embed"
  ["rag-mm-embed"]="rag-mm-embed"
  ["rag-reranker"]="rag-reranker"
  ["paddleocr-vl"]="paddleocr-vl"
)

declare -A POD_GPUS=(
  ["chat-llm"]="$GPU_ID"
  ["rag-text-embed"]="NVIDIA GeForce RTX 3090"
  ["rag-mm-embed"]="NVIDIA GeForce RTX 3090"
  ["rag-reranker"]="NVIDIA GeForce RTX 3090"
  ["paddleocr-vl"]="NVIDIA GeForce RTX 3090"
)

# Check if all expected pods are already running
ALL_EXIST=true
for key in "${!POD_NAMES[@]}"; do
  id=$(existing_pod_id "$key")
  if [[ -z "$id" ]]; then
    ALL_EXIST=false
    break
  fi
done

# ═════════════════════════════════════════════════════════════════════════════
# 1. CHAT LLM — google/gemma-4-E4B-it (requires HF token for gated model)
# ═════════════════════════════════════════════════════════════════════════════
source "$SCRIPT_DIR/lora-adapters.sh"
LORA_REPO="${LORA_REPO:-}"
BASE_START_CMD="exec python3 -m vllm.entrypoints.openai.api_server --model google/gemma-4-E4B-it --trust-remote-code --port 8000 --gpu-memory-utilization 0.90 --max-model-len 32768 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 --chat-template-content-format auto"
LORA_START_CMD="$BASE_START_CMD"

CHAT_POD=""; CHAT_TPL=""
if $ALL_EXIST && ! $REFRESH_ENV; then
  CHAT_POD=$(existing_pod_id "chat-llm")
  echo "  chat-llm already exists (id=$CHAT_POD), skipping creation."
else
  if [[ -n "$LORA_REPO" ]]; then
    LORA_CACHE_DIR="${LORA_CACHE_DIR:-/tmp/lora_adapters}"
    if [[ ! -d "$LORA_CACHE_DIR" ]]; then
      echo "  Cloning LoRA repo $LORA_REPO -> $LORA_CACHE_DIR"
      git clone --depth 1 "$LORA_REPO" "$LORA_CACHE_DIR"
    fi
    discover_lora_adapters "$LORA_CACHE_DIR" "google/gemma-4-E4B-it"
    if (( ${#LORA_NAMES[@]} > 0 )); then
      LORA_MODULES_ARGS="--enable-lora --lora-modules"
      for i in "${!LORA_NAMES[@]}"; do
        relative_dir="${LORA_DIRS[$i]#${LORA_CACHE_DIR}/}"
        LORA_MODULES_ARGS+=" ${LORA_NAMES[$i]}=/lora_adapters/${relative_dir}"
      done
      LORA_MODULES_ARGS+=" --max-lora-rank $LORA_MAX_RANK"
      LORA_START_CMD="mkdir -p /lora_adapters && cp -r ${LORA_CACHE_DIR}/* /lora_adapters/ && ${BASE_START_CMD} ${LORA_MODULES_ARGS}"
    fi
  fi
  CHAT_TPL=$(create_template "chat-llm-$TS" \
    --image "vllm/vllm-openai:latest" \
    --docker-entrypoint "/bin/bash" \
    --docker-start-cmd "-c,${LORA_START_CMD}" \
    --env "$HF_ENV_JSON" \
    --ports "8000/http" \
    --container-disk-in-gb 50)
  echo "Creating chat-llm pod (${POD_GPUS[chat-llm]})..."
  CHAT_POD=$(create_pod "chat-llm" \
    --template-id "$CHAT_TPL" \
    --gpu-id "${POD_GPUS[chat-llm]}" \
    --cloud-type "$CLOUD_TYPE" \
    --container-disk-in-gb 50)
fi

# ═════════════════════════════════════════════════════════════════════════════
# 2–5: Other model pods (same skip-if-exists pattern, no LoRA config)
# ═════════════════════════════════════════════════════════════════════════════

TEXT_POD=""; TEXT_TPL=""
if $ALL_EXIST && ! $REFRESH_ENV && [[ -z "$(existing_pod_id rag-text-embed)" ]]; then
  : # will be created below
elif $ALL_EXIST && ! $REFRESH_ENV; then
  TEXT_POD=$(existing_pod_id "rag-text-embed")
  echo "  rag-text-embed already exists (id=$TEXT_POD), skipping."
else
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
    --gpu-id "${POD_GPUS[rag-text-embed]}" \
    --cloud-type "$CLOUD_TYPE" \
    --container-disk-in-gb 50)
fi

MM_POD=""; MM_TPL=""
if $ALL_EXIST && ! $REFRESH_ENV; then
  MM_POD=$(existing_pod_id "rag-mm-embed")
  echo "  rag-mm-embed already exists (id=$MM_POD), skipping."
else
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
    --gpu-id "${POD_GPUS[rag-mm-embed]}" \
    --cloud-type "$CLOUD_TYPE" \
    --container-disk-in-gb 50)
fi

RERANKER_POD=""; RERANKER_TPL=""
if $ALL_EXIST && ! $REFRESH_ENV; then
  RERANKER_POD=$(existing_pod_id "rag-reranker")
  echo "  rag-reranker already exists (id=$RERANKER_POD), skipping."
else
  RERANKER_ENV_JSON=$(python3 -c "
import json
d = {'HF_TOKEN': '${HF_TOKEN}', 'HUGGING_FACE_HUB_TOKEN': '${HF_TOKEN}', 'HF_OVERRIDES': json.dumps({'architectures': ['Qwen3VLForSequenceClassification'], 'classifier_from_token': ['no', 'yes'], 'is_original_qwen3_reranker': True})}
print(json.dumps(d))")
  echo "Creating reranker template..."
  RERANKER_TPL=$(create_template "rag-reranker-$TS" \
    --image "vllm/vllm-openai:latest" \
    --docker-entrypoint "/bin/bash" \
    --docker-start-cmd '-c,exec python3 -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-VL-Reranker-2B --runner pooling --trust-remote-code --port 8000 --gpu-memory-utilization 0.90 --max-model-len 4096 --hf-overrides "$HF_OVERRIDES"' \
    --env "$RERANKER_ENV_JSON" \
    --ports "8000/http" \
    --container-disk-in-gb 50)
  echo "Creating reranker pod..."
  RERANKER_POD=$(create_pod "rag-reranker" \
    --template-id "$RERANKER_TPL" \
    --gpu-id "${POD_GPUS[rag-reranker]}" \
    --cloud-type "$CLOUD_TYPE" \
    --container-disk-in-gb 50)
fi

OCR_POD=""; OCR_TPL=""
if $ALL_EXIST && ! $REFRESH_ENV; then
  OCR_POD=$(existing_pod_id "paddleocr-vl")
  echo "  paddleocr-vl already exists (id=$OCR_POD), skipping."
else
  echo "Creating PaddleOCR template..."
  OCR_TPL=$(create_template "paddleocr-vl-$TS" \
    --image "vllm/vllm-openai:latest" \
    --docker-entrypoint "/bin/bash" \
    --docker-start-cmd "-c,exec python3 -m vllm.entrypoints.openai.api_server --model PaddlePaddle/PaddleOCR-VL-1.6 --trust-remote-code --port 8000 --gpu-memory-utilization 0.85 --max-num-batched-tokens 16384 --no-enable-prefix-caching --mm-processor-cache-gb 0" \
    --env "$HF_ENV_JSON" \
    --ports "8000/http" \
    --container-disk-in-gb 50)
  echo "Creating PaddleOCR pod..."
  OCR_POD=$(create_pod "paddleocr-vl" \
    --template-id "$OCR_TPL" \
    --gpu-id "${POD_GPUS[paddleocr-vl]}" \
    --cloud-type "$CLOUD_TYPE" \
    --container-disk-in-gb 50)
fi

trap - ERR

echo ""
echo "All pods deployed."
echo "  chat-llm       ${POD_GPUS[chat-llm]}        $CHAT_POD"
echo "  text-embed     ${POD_GPUS[rag-text-embed]}       $TEXT_POD"
echo "  mm-embed       ${POD_GPUS[rag-mm-embed]}       $MM_POD"
echo "  reranker       ${POD_GPUS[rag-reranker]}       $RERANKER_POD"
echo "  PaddleOCR      ${POD_GPUS[paddleocr-vl]}       $OCR_POD"

if $NO_WAIT; then
  echo ""
  # Write partial env with just pod IDs (for teardown)
  cat > "$ENV_FILE" <<EOF
CHAT_POD=$CHAT_POD
TEXT_POD=$TEXT_POD
MM_POD=$MM_POD
RERANKER_POD=$RERANKER_POD
OCR_POD=$OCR_POD
EOF
  echo "Pod IDs saved to $ENV_FILE"
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


echo ""
echo "Waiting for pods to reach RUNNING..."
wait_running "chat-llm"       "$CHAT_POD"
wait_running "text-embed"    "$TEXT_POD"
wait_running "mm-embed"      "$MM_POD"
wait_running "reranker"      "$RERANKER_POD"
wait_running "PaddleOCR"     "$OCR_POD"

CHAT_URL="https://${CHAT_POD}-8000.proxy.runpod.net"
TEXT_URL="https://${TEXT_POD}-8000.proxy.runpod.net"
MM_URL="https://${MM_POD}-8000.proxy.runpod.net"
RERANKER_URL="https://${RERANKER_POD}-8000.proxy.runpod.net"
OCR_URL="https://${OCR_POD}-8000.proxy.runpod.net"

if ! $NO_HEALTHCHECK; then
  bash "$SCRIPT_DIR/wait-health.sh" \
    "chat-llm=$CHAT_URL/health" \
    "text-embed=$TEXT_URL/health" \
    "mm-embed=$MM_URL/health" \
    "reranker=$RERANKER_URL/health" \
    "paddleocr=$OCR_URL/health"
fi

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
TEXT_DIM=$(detect_dim "$TEXT_URL" "nvidia/llama-embed-nemotron-8b") || true
MM_DIM=$(detect_dim "$MM_URL" "nvidia/nemotron-colembed-vl-8b-v2" "/pooling") || true
TEXT_DIM="${TEXT_DIM:-4096}"
MM_DIM="${MM_DIM:-4096}"

# Write full .env.runpod (no LORA_ADAPTERS — discovered at runtime via /v1/models)
{
  echo "# RunPod endpoints (auto-generated by deploy-runpod.sh)"
  echo "CHAT_BASE_URL=${CHAT_URL}/v1"
  echo "CHAT_API_KEY=dummy"
  echo "CHAT_MODEL=openai/google/gemma-4-E4B-it"
  echo "VISION_MODEL=openai/google/gemma-4-E4B-it"
  echo ""
  echo "EMBEDDING_BASE_URL=${TEXT_URL}/v1"
  echo "EMBEDDING_API_KEY=dummy"
  echo "TEXT_EMBEDDING_MODEL=nvidia/llama-embed-nemotron-8b"
  echo "TEXT_EMBEDDING_DIM=${TEXT_DIM}"
  echo ""
  echo "MULTIMODAL_EMBEDDING_BASE_URL=${MM_URL}"
  echo "MULTIMODAL_EMBEDDING_API_KEY=dummy"
  echo "MULTIMODAL_EMBEDDING_MODEL=nvidia/nemotron-colembed-vl-8b-v2"
  echo "MULTIMODAL_EMBEDDING_DIM=${MM_DIM}"
  echo ""
  echo "RERANKER_BASE_URL=${RERANKER_URL}"
  echo "RERANKER_API_KEY=dummy"
  echo "RERANKER_MODEL=Qwen/Qwen3-VL-Reranker-2B"
  echo ""
  echo "OCR_MODE=paddleocr"
  echo "OCR_BASE_URL=${OCR_URL}/v1"
  echo "OCR_API_KEY=dummy"
  echo "OCR_MODEL=PaddlePaddle/PaddleOCR-VL-1.6"
  echo ""
  echo "# Pod IDs (for cleanup)"
  echo "CHAT_POD=$CHAT_POD"
  echo "CHAT_TPL=$CHAT_TPL"
  echo "TEXT_POD=$TEXT_POD"
  echo "TEXT_TPL=$TEXT_TPL"
  echo "MM_POD=$MM_POD"
  echo "MM_TPL=$MM_TPL"
  echo "RERANKER_POD=$RERANKER_POD"
  echo "RERANKER_TPL=$RERANKER_TPL"
  echo "OCR_POD=$OCR_POD"
  echo "OCR_TPL=$OCR_TPL"
} > "$ENV_FILE"

echo ""
echo "══════════════════════════════════════════════════"
echo " All RunPod models ready!"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM        $CHAT_URL"
echo "  Text Embed      $TEXT_URL  (dim: $TEXT_DIM)"
echo "  MM Embed        $MM_URL    (dim: $MM_DIM)"
echo "  Reranker        $RERANKER_URL"
echo "  PaddleOCR       $OCR_URL"
