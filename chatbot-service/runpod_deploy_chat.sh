#!/bin/bash
# Deploy a RunPod GPU pod serving google/gemma-4-E4B-it via vLLM.
# The chatbot-service stack (postgres, file-server, web, worker) runs locally
# in Docker and connects to this pod as the LLM endpoint.
#
# Cheapest GPU with enough VRAM is typically RTX 3090 (24GB, ~$0.22/hr on community).
# Override via: GPU_ID="NVIDIA GeForce RTX 4090" bash runpod_deploy_chat.sh
#
# Usage:
#   export HF_TOKEN=hf_...
#   bash runpod_deploy_chat.sh               # deploy + wait for readiness
#   bash runpod_deploy_chat.sh --no-wait     # deploy only, run runpod_wait_chat.sh separately
#
# Outputs:
#   .runpod_chat_state   pod ID and URL (sourced by other scripts)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

GPU_ID="${GPU_ID:-NVIDIA GeForce RTX 4090}"
CLOUD_TYPE="${CLOUD_TYPE:-community}"
NO_WAIT=false
[[ "${1:-}" == "--no-wait" ]] && NO_WAIT=true

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ -z "${HF_TOKEN:-}" ]]; then
  cat <<'ERR'
ERROR: HF_TOKEN is required.

Before running this script:
   1. Accept the license on HuggingFace (logged in):
        https://huggingface.co/google/gemma-4-E4B-it
  2. Create a token at https://huggingface.co/settings/tokens
  3. export HF_TOKEN=<your_token>
ERR
  exit 1
fi

STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"
TS=$(date +%s)
HF_ENV_JSON="{\"HF_TOKEN\":\"${HF_TOKEN}\",\"HUGGING_FACE_HUB_TOKEN\":\"${HF_TOKEN}\"}"

CREATED_TPL=""
CREATED_POD=""

cleanup_on_error() {
  echo ""
  echo "Error encountered — cleaning up..."
  [[ -n "$CREATED_POD" ]] && runpodctl pod delete "$CREATED_POD" 2>/dev/null && echo "  deleted pod $CREATED_POD" || true
  [[ -n "$CREATED_TPL" ]] && runpodctl template delete "$CREATED_TPL" 2>/dev/null && echo "  deleted template $CREATED_TPL" || true
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
  CREATED_TPL="$id"
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
  CREATED_POD="$id"
  echo "$id"
}

# ── Deploy chat LLM pod (google/gemma-4-E4B-it) ─────────────────────────
echo "Creating chat-llm template..."
CHAT_TPL=$(create_template "chat-llm-$TS" \
  --image "vllm/vllm-openai:latest" \
  --docker-entrypoint "/bin/bash" \
  --docker-start-cmd "-c,exec python3 -m vllm.entrypoints.openai.api_server --model google/gemma-4-E4B-it --trust-remote-code --port 8000 --gpu-memory-utilization 0.90 --max-model-len 32768 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 --chat-template-content-format auto" \
  --env "$HF_ENV_JSON" \
  --ports "8000/http" \
  --container-disk-in-gb 50)
echo "  template: $CHAT_TPL"

echo "Creating chat-llm pod ($GPU_ID)..."
CHAT_POD=$(create_pod "chat-llm" \
  --template-id "$CHAT_TPL" \
  --gpu-id "$GPU_ID" \
  --cloud-type "$CLOUD_TYPE" \
  --container-disk-in-gb 50)
echo "  pod: $CHAT_POD"

# ── Persist state ────────────────────────────────────────────────────────────
cat > "$STATE_FILE" <<EOF
CHAT_POD=$CHAT_POD
CHAT_TPL=$CHAT_TPL
EOF

trap - ERR

echo ""
echo "Chat LLM pod provisioning on RunPod. ID saved to $STATE_FILE"
echo "  chat-llm ($GPU_ID) $CHAT_POD"

if $NO_WAIT; then
  echo ""
  echo "Run 'bash runpod_wait_chat.sh' once the pod shows RUNNING in 'runpodctl pod list'."
  exit 0
fi

bash "$SCRIPT_DIR/runpod_wait_chat.sh"
