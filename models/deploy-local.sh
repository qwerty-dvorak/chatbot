#!/bin/bash
# Start all local model serving containers via Docker.
# - Gemma inference server (vLLM, requires 2x GPUs)
# - Text embedding server (vLLM, 1 GPU)
# - Multimodal embedding server (vLLM, 1 GPU)
# - Reranker server (vLLM, 1 GPU)
#
# Models are mounted from the sibling ../models/ directory (outside this repo).
#
# Usage:
#   bash deploy-local.sh
#   bash deploy-local.sh --clean   # tear down and restart
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/models"
NETWORK_NAME="model_serving_net"
LORA_ADAPTERS_DIR="${LORA_ADAPTERS_DIR:-$MODEL_DIR/../lora_adapters}"
if [[ -d "$LORA_ADAPTERS_DIR" ]]; then
  LORA_ADAPTERS_DIR="$(cd "$LORA_ADAPTERS_DIR" && pwd)"
fi
LOCAL_CHAT_MODEL_ID="${LOCAL_CHAT_MODEL_ID:-google/gemma-4-26B-A4B-it}"
source "$SCRIPT_DIR/lora-adapters.sh"

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"

if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old model containers..."
  docker rm -f gemma-inference rag-text-embed rag-multimodal-embed rag-reranker 2>/dev/null || true
fi

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
persist_lora_names "$SCRIPT_DIR/.env.local" "$LORA_NAMES_CSV"

echo "Starting Gemma Inference Server (GPU 0,1)..."
docker rm -f gemma-inference 2>/dev/null || true

docker run -d \
  --name gemma-inference \
  --network "$NETWORK_NAME" \
  --shm-size="16gb" \
  --gpus '"device=0,1"' \
  -v "$MODEL_DIR/gemma-4-26B-A4B-it:/model" \
  "${LORA_MOUNT_ARGS[@]}" \
  -p 8430:8000 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/v1/models\")" 2>/dev/null && echo ok' \
  --health-interval=10s --health-timeout=5s --health-retries=15 --health-start-period=60s \
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

echo "Starting Text Embedding Server (GPU 0)..."
docker rm -f rag-text-embed 2>/dev/null || true
docker run -d \
  --name rag-text-embed \
  --network "$NETWORK_NAME" \
  --gpus '"device=0"' \
  --shm-size=16g \
  -v "$MODEL_DIR/llama-embed-nemotron-8b:/model" \
  -p 8090:8000 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model \
  --trust-remote-code \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192

echo "Starting Multimodal Embedding Server (GPU 1)..."
docker rm -f rag-multimodal-embed 2>/dev/null || true
docker run -d \
  --name rag-multimodal-embed \
  --network "$NETWORK_NAME" \
  --gpus '"device=1"' \
  --shm-size=16g \
  -v "$MODEL_DIR/nemotron-colembed-vl-8b-v2:/model" \
  -p 8091:8000 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model \
  --trust-remote-code \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --limit-mm-per-prompt '{"image": 1, "video": 0}' \
  --skip-mm-profiling

echo "Starting Reranker Server (GPU 2)..."
docker rm -f rag-reranker 2>/dev/null || true
docker run -d \
  --name rag-reranker \
  --network "$NETWORK_NAME" \
  --gpus '"device=2"' \
  --shm-size=16g \
  -v "$MODEL_DIR/Qwen3-VL-Reranker-2B:/model" \
  -p 8092:8000 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model \
  --trust-remote-code \
  --gpu-memory-utilization 0.90 \
  --hf-overrides '{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'

echo ""
echo "All local model servers started."
echo "  Gemma Inference      -> http://localhost:8430/v1  (GPU 0,1)"
echo "  Text Embedding       -> http://localhost:8090/v1  (GPU 0)"
echo "  Multimodal Embedding -> http://localhost:8091     (GPU 1)"
echo "  Reranker             -> http://localhost:8092     (GPU 2)"
