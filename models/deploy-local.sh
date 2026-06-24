#!/bin/bash
# Start all local model serving containers via Docker.
# - Gemma inference server (vLLM, requires 2x GPUs)
# - Text embedding server (vLLM, 1 GPU)
# - Multimodal embedding server (vLLM, 1 GPU)
# - Reranker server (vLLM, 1 GPU)
# - PaddleOCR server (vLLM, 1 GPU)
#
# Models are mounted from the sibling ../models/ directory (outside this repo).
# PaddleOCR uses the HF model ID directly (not cloned locally).
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
  docker rm -f gemma-inference rag-text-embed rag-multimodal-embed rag-reranker rag-ocr 2>/dev/null || true
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
  --runner pooling \
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
  --runner pooling \
  --gpu-memory-utilization 0.90 \
  --max-model-len 4096 \
  --hf-overrides '{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'

echo "Starting PaddleOCR Server (GPU 2)..."
docker rm -f rag-ocr 2>/dev/null || true
docker run -d \
  --name rag-ocr \
  --network "$NETWORK_NAME" \
  --gpus '"device=2"' \
  --shm-size=16g \
  -p 8093:8000 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model PaddlePaddle/PaddleOCR-VL-1.6 \
  --trust-remote-code \
  --gpu-memory-utilization 0.85 \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0

# ── Wait for health ──────────────────────────────────────────────────────
bash "$SCRIPT_DIR/wait-health.sh" \
  "chat-llm=http://localhost:8430/health" \
  "text-embed=http://localhost:8090/health" \
  "mm-embed=http://localhost:8091/health" \
  "reranker=http://localhost:8092/health" \
  "paddleocr=http://localhost:8093/health"

# ── Detect embedding dimensions ──────────────────────────────────────────
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
TEXT_DIM=$(detect_dim "http://localhost:8090" "nvidia/llama-embed-nemotron-8b") || true
MM_DIM=$(detect_dim "http://localhost:8091" "nvidia/nemotron-colembed-vl-8b-v2" "/pooling") || true
TEXT_DIM="${TEXT_DIM:-4096}"
MM_DIM="${MM_DIM:-4096}"

# ── Write .env.local ─────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env.local"
{
  echo "# Local endpoints (auto-generated by deploy-local.sh)"
  echo "CHAT_BASE_URL=http://localhost:8430/v1"
  echo "CHAT_API_KEY=dummy"
  echo "CHAT_MODEL=$LOCAL_CHAT_MODEL_ID"
  echo "VISION_MODEL=$LOCAL_CHAT_MODEL_ID"
  echo ""
  echo "EMBEDDING_BASE_URL=http://localhost:8090/v1"
  echo "EMBEDDING_API_KEY=dummy"
  echo "TEXT_EMBEDDING_MODEL=nvidia/llama-embed-nemotron-8b"
  echo "TEXT_EMBEDDING_DIM=${TEXT_DIM}"
  echo ""
  echo "MULTIMODAL_EMBEDDING_BASE_URL=http://localhost:8091"
  echo "MULTIMODAL_EMBEDDING_API_KEY=dummy"
  echo "MULTIMODAL_EMBEDDING_MODEL=nvidia/nemotron-colembed-vl-8b-v2"
  echo "MULTIMODAL_EMBEDDING_DIM=${MM_DIM}"
  echo ""
  echo "RERANKER_BASE_URL=http://localhost:8092"
  echo "RERANKER_API_KEY=dummy"
  echo "RERANKER_MODEL=Qwen/Qwen3-VL-Reranker-2B"
  echo ""
  echo "OCR_MODE=paddleocr"
  echo "OCR_BASE_URL=http://localhost:8093/v1"
  echo "OCR_API_KEY=dummy"
  echo "OCR_MODEL=PaddlePaddle/PaddleOCR-VL-1.6"
} > "$ENV_FILE"

echo ""
echo "══════════════════════════════════════════════════"
echo " All local models ready!"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM        http://localhost:8430  (GPU 0,1)"
echo "  Text Embed      http://localhost:8090  (dim: $TEXT_DIM)"
echo "  MM Embed        http://localhost:8091  (dim: $MM_DIM)"
echo "  Reranker        http://localhost:8092"
echo "  PaddleOCR       http://localhost:8093"
