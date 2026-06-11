#!/bin/bash
# Deploy 3 RunPod GPU pods for RAG pipeline testing + start local Milvus:
#   - text-embed: RTX 3090, vllm/vllm-openai:latest + nvidia/llama-embed-nemotron-8b
#   - mm-embed:   RTX 3090, vllm/vllm-openai:latest + nvidia/nemotron-colembed-vl-8b-v2
#   - reranker:   RTX 3090, vllm/vllm-openai:latest + Qwen/Qwen3-VL-Reranker-2B
#   - milvus:     local Docker (milvusdb/milvus:latest) via runpod_start_local_milvus.sh
#
# Uses the cheapest GPU with enough VRAM (24 GB) for 8B-parameter models:
#   NVIDIA GeForce RTX 3090. Override via:
#     GPU_ID="NVIDIA GeForce RTX 4090" bash runpod_deploy.sh
#
# Usage:
#   export HF_TOKEN=<your_hf_token>
#   bash runpod_deploy.sh               # deploy + wait for readiness
#   bash runpod_deploy.sh --no-wait     # deploy only, run runpod_wait.sh separately
#
# Outputs:
#   .runpod_state   pod IDs and URLs (sourced by other scripts)
#   .env.runpod     ready-to-use .env for the RAG API
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

GPU_ID="${GPU_ID:-NVIDIA GeForce RTX 3090}"
CLOUD_TYPE="${CLOUD_TYPE:-community}"
NO_WAIT=false
[[ "${1:-}" == "--no-wait" ]] && NO_WAIT=true

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ -z "${HF_TOKEN:-}" ]]; then
  cat <<'ERR'
ERROR: HF_TOKEN is required.

Before running this script:
  1. Accept licenses on HuggingFace (logged in):
       https://huggingface.co/nvidia/llama-embed-nemotron-8b
       https://huggingface.co/nvidia/nemotron-colembed-vl-8b-v2
  2. Create a token at https://huggingface.co/settings/tokens
  3. export HF_TOKEN=<your_token>
ERR
  exit 1
fi

STATE_FILE="$SCRIPT_DIR/.runpod_state"
ENV_FILE="$SCRIPT_DIR/.env.runpod"
TS=$(date +%s)
# --env flag expects a JSON object string
HF_ENV_JSON="{\"HF_TOKEN\":\"${HF_TOKEN}\",\"HUGGING_FACE_HUB_TOKEN\":\"${HF_TOKEN}\"}"

# Track created resources for cleanup on failure
CREATED_TPLS=()
CREATED_PODS=()

cleanup_on_error() {
  echo ""
  echo "Error encountered — cleaning up created resources..."
  for pod_id in "${CREATED_PODS[@]}"; do
    runpodctl pod delete "$pod_id" 2>/dev/null && echo "  deleted pod $pod_id" || true
  done
  for tpl_id in "${CREATED_TPLS[@]}"; do
    runpodctl template delete "$tpl_id" 2>/dev/null && echo "  deleted template $tpl_id" || true
  done
}
trap cleanup_on_error ERR

# ── Helper: create template, echo id ─────────────────────────────────────────
create_template() {
  local name="$1"; shift
  local out
  out=$(runpodctl template create --name "$name" "$@" 2>&1)
  local id
  id=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null) \
    || { echo "  Template create failed: $out" >&2; exit 1; }
  CREATED_TPLS+=("$id")
  echo "$id"
}

# ── Helper: create pod from template, echo id ────────────────────────────────
create_pod() {
  local name="$1"; shift
  local out
  out=$(runpodctl pod create --name "$name" "$@" 2>&1)
  local id
  id=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null) \
    || { echo "  Pod create failed: $out" >&2; exit 1; }
  CREATED_PODS+=("$id")
  echo "$id"
}

# CMD format for vLLM pods: comma-separated "-c,script"
# RunPod splits comma-separated --docker-start-cmd into multiple JSON array
# elements: ["-c", "exec python3 ..."].  Docker (exec form) passes these as
# separate argv to /bin/bash: argv[1]="-c" argv[2]="exec python3 ...".
# Bash requires -c and the script as SEPARATE argv elements; combined
# "-cSCRIPT" in a single arg does NOT work (bash treats trailing chars as
# more option flags, not as the script argument).

# ─────────────────────────────────────────────────────────────────────────────
# 1. LOCAL MILVUS — milvusdb/milvus:latest via Docker on this machine
#    RunPod community cloud CPU pods don't expose TCP ports reliably; local Docker
#    is the pragmatic solution for testing.
# ─────────────────────────────────────────────────────────────────────────────
echo "Starting local Milvus (milvusdb/milvus:latest)..."
bash "$SCRIPT_DIR/runpod_start_local_milvus.sh"

# ─────────────────────────────────────────────────────────────────────────────
# 2. TEXT EMBEDDING — nvidia/llama-embed-nemotron-8b
# ─────────────────────────────────────────────────────────────────────────────
echo "Creating text-embed template..."
TEXT_TPL=$(create_template "rag-text-embed-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd "-c,exec python3 -m vllm.entrypoints.openai.api_server --model nvidia/llama-embed-nemotron-8b --trust-remote-code --task embed --port 8000 --gpu-memory-utilization 0.90 --max-model-len 32768" \
  --env "$HF_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)
echo "  template: $TEXT_TPL"

echo "Creating text-embed pod (RTX 3090)..."
TEXT_POD=$(create_pod "rag-text-embed" \
  --template-id "$TEXT_TPL" \
  --gpu-id "$GPU_ID" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)
echo "  pod: $TEXT_POD"

# ─────────────────────────────────────────────────────────────────────────────
# 3. MULTIMODAL EMBEDDING — nvidia/nemotron-colembed-vl-8b-v2
# ─────────────────────────────────────────────────────────────────────────────
echo "Creating multimodal-embed template..."
MM_TPL=$(create_template "rag-mm-embed-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd '-c,exec python3 -m vllm.entrypoints.openai.api_server --model nvidia/nemotron-colembed-vl-8b-v2 --trust-remote-code --runner pooling --port 8000 --gpu-memory-utilization 0.90 --max-model-len 8192 --limit-mm-per-prompt '"'"'{"image":1,"video":0}'"'"' --skip-mm-profiling' \
  --env "$HF_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)
echo "  template: $MM_TPL"

echo "Creating multimodal-embed pod (RTX 3090)..."
MM_POD=$(create_pod "rag-mm-embed" \
  --template-id "$MM_TPL" \
  --gpu-id "$GPU_ID" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)
echo "  pod: $MM_POD"

# ─────────────────────────────────────────────────────────────────────────────
# 4. RERANKER — Qwen/Qwen3-VL-Reranker-2B (vLLM /score endpoint)
#    hf-overrides JSON goes in env var HF_OVERRIDES to avoid shell-quoting issues.
# ─────────────────────────────────────────────────────────────────────────────
RERANKER_ENV_JSON=$(python3 -c "
import json
d = {
    'HF_TOKEN': '${HF_TOKEN}',
    'HUGGING_FACE_HUB_TOKEN': '${HF_TOKEN}',
    'HF_OVERRIDES': json.dumps({'architectures': ['Qwen3VLForSequenceClassification'], 'classifier_from_token': ['no','yes'], 'is_original_qwen3_reranker': True})
}
print(json.dumps(d))")

echo "Creating reranker template..."
RERANKER_TPL=$(create_template "rag-reranker-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd '-c,exec python3 -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3-VL-Reranker-2B --trust-remote-code --task score --port 8000 --gpu-memory-utilization 0.90 --hf-overrides "$HF_OVERRIDES"' \
  --env "$RERANKER_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)
echo "  template: $RERANKER_TPL"

echo "Creating reranker pod (RTX 3090)..."
RERANKER_POD=$(create_pod "rag-reranker" \
  --template-id "$RERANKER_TPL" \
  --gpu-id "$GPU_ID" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)
echo "  pod: $RERANKER_POD"

# ── Persist state (all pods created successfully) ────────────────────────────
cat > "$STATE_FILE" <<EOF
TEXT_POD=$TEXT_POD
TEXT_TPL=$TEXT_TPL
MM_POD=$MM_POD
MM_TPL=$MM_TPL
RERANKER_POD=$RERANKER_POD
RERANKER_TPL=$RERANKER_TPL
EOF

# Disable error trap now that state is saved (teardown.sh handles cleanup)
trap - ERR

echo ""
echo "3 RunPod pods provisioning + local Milvus running. IDs saved to $STATE_FILE"
echo ""
echo "  milvus         (local)   localhost:19530"
echo "  rag-text-embed (RTX3090) $TEXT_POD"
echo "  rag-mm-embed   (RTX3090) $MM_POD"
echo "  rag-reranker   (RTX3090) $RERANKER_POD"

if $NO_WAIT; then
  echo ""
  echo "Run 'bash runpod_wait.sh' once pods show RUNNING in 'runpodctl pod list'."
  exit 0
fi

bash "$SCRIPT_DIR/runpod_wait.sh"
