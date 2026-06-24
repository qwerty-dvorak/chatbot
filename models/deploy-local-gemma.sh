#!/bin/bash
# Start the Gemma inference server via Docker (2x GPUs, tensor parallel).
# Models are mounted from the sibling ../models/ directory (outside this repo).
#
# Usage:
#   bash deploy-local-gemma.sh
#   bash deploy-local-gemma.sh --clean            # tear down and restart
#   bash deploy-local-gemma.sh --no-healthcheck   # skip health check configuration
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/models"
LORA_ADAPTERS_DIR="${LORA_ADAPTERS_DIR:-$MODEL_DIR/../lora_adapters}"
if [[ -d "$LORA_ADAPTERS_DIR" ]]; then
  LORA_ADAPTERS_DIR="$(cd "$LORA_ADAPTERS_DIR" && pwd)"
fi
LOCAL_CHAT_MODEL_ID="${LOCAL_CHAT_MODEL_ID:-google/gemma-4-26B-A4B-it}"
source "$SCRIPT_DIR/lora-adapters.sh"
NO_HEALTHCHECK="${NO_HEALTHCHECK:-false}"

for arg in "$@"; do
  case "$arg" in
    --clean) echo "Removing gemma-inference..."; docker rm -f gemma-inference 2>/dev/null || true ;;
    --no-healthcheck) NO_HEALTHCHECK=true ;;
  esac
done

discover_lora_adapters "$LORA_ADAPTERS_DIR" "$LOCAL_CHAT_MODEL_ID"
LORA_MOUNT_ARGS=()
LORA_ARGS=()
if (( ${#LORA_NAMES[@]} > 0 )); then
  LORA_MOUNT_ARGS=(-v "$LORA_ADAPTERS_DIR:/lora_adapters:ro")
  LORA_ARGS=(--enable-lora --lora-modules)
  for i in "${!LORA_NAMES[@]}"; do
    relative_dir="${LORA_DIRS[$i]#${LORA_ADAPTERS_DIR}/}"
    LORA_ARGS+=("${LORA_NAMES[$i]}=/lora_adapters/${relative_dir}")
    echo "  LoRA adapter found: ${LORA_NAMES[$i]} ($relative_dir)"
  done
  LORA_ARGS+=(--max-lora-rank "$LORA_MAX_RANK")
fi

HEALTH_ARGS=()
if ! $NO_HEALTHCHECK; then
  HEALTH_ARGS=(
    --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/v1/models\")" 2>/dev/null && echo ok'
    --health-interval=10s --health-timeout=5s --health-retries=15 --health-start-period=60s
  )
fi

echo "Starting Gemma Inference Server (GPU 0,1)..."
docker rm -f gemma-inference 2>/dev/null || true
docker run -d \
  --name gemma-inference \
  --shm-size="16gb" \
  --gpus '"device=0,1"' \
  -v "$MODEL_DIR/gemma-4-26B-A4B-it:/model" \
  "${LORA_MOUNT_ARGS[@]}" \
  -p 8430:8000 \
  "${HEALTH_ARGS[@]}" \
  vllm/vllm-openai:latest \
  --model /model \
  --tensor-parallel-size 2 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.85 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  "${LORA_ARGS[@]}"

echo ""
echo "Gemma inference server started on http://localhost:8430"
