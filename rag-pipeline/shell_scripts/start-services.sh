#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="rag_net"
API_IMAGE="rag-api"
VOL_DIR="${DOCKER_VOLUME_DIRECTORY:-$SERVICE_DIR/volumes}"
DATA_DIR="$SERVICE_DIR/data"
MODEL_DIR="$(cd "$ROOT_DIR/../.." && pwd)/models"  # sibling ../models/ repo

# Load model endpoints from models/.env.local
[ -f "$ROOT_DIR/models/.env.local" ] && export $(grep -v '^#' "$ROOT_DIR/models/.env.local" | xargs)

TEXT_EMBED_PORT="${TEXT_EMBED_PORT:-8090}"
MULTIMODAL_PORT="${MULTIMODAL_PORT:-8091}"
RERANKER_PORT="${RERANKER_PORT:-8092}"
API_PORT="${API_PORT:-8093}"
MILVUS_PORT="${MILVUS_PORT:-19530}"
MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"

TEXT_EMBED_GPU="${TEXT_EMBED_GPU:-0}"
MULTIMODAL_GPU="${MULTIMODAL_GPU:-1}"
RERANKER_GPU="${RERANKER_GPU:-2}"
TP_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old containers..."
  docker rm -f rag-text-embed rag-multimodal-embed rag-reranker rag-api 2>/dev/null || true
fi

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"

mkdir -p "$VOL_DIR/etcd" "$VOL_DIR/minio" "$VOL_DIR/milvus"

# ── Text embedding server ─────────────────────────────────────────────────────
echo "Starting text embedding server (GPU $TEXT_EMBED_GPU, port $TEXT_EMBED_PORT)..."
docker rm -f rag-text-embed 2>/dev/null || true
docker run -d \
  --name rag-text-embed \
  --network "$NETWORK_NAME" \
  --gpus "\"device=$TEXT_EMBED_GPU\"" \
  --shm-size=16g \
  -v "$MODEL_DIR/llama-embed-nemotron-8b:/model" \
  -p "$TEXT_EMBED_PORT:8000" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model --trust-remote-code --tensor-parallel-size "$TP_SIZE" \
  --gpu-memory-utilization 0.90 --max-model-len 8192

# ── Multimodal embedding server ───────────────────────────────────────────────
echo "Starting multimodal embedding server (GPU $MULTIMODAL_GPU, port $MULTIMODAL_PORT)..."
docker rm -f rag-multimodal-embed 2>/dev/null || true
docker run -d \
  --name rag-multimodal-embed \
  --network "$NETWORK_NAME" \
  --gpus "\"device=$MULTIMODAL_GPU\"" \
  --shm-size=16g \
  -v "$MODEL_DIR/nemotron-colembed-vl-8b-v2:/model" \
  -p "$MULTIMODAL_PORT:8000" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model --trust-remote-code --tensor-parallel-size "$TP_SIZE" \
  --gpu-memory-utilization 0.90 --max-model-len 8192 \
  --limit-mm-per-prompt '{"image": 1, "video": 0}' --skip-mm-profiling

# ── Reranker server ───────────────────────────────────────────────────────────
echo "Starting reranker server (GPU $RERANKER_GPU, port $RERANKER_PORT)..."
docker rm -f rag-reranker 2>/dev/null || true
docker run -d \
  --name rag-reranker \
  --network "$NETWORK_NAME" \
  --gpus "\"device=$RERANKER_GPU\"" \
  --shm-size=16g \
  -v "$MODEL_DIR/Qwen3-VL-Reranker-2B:/model" \
  -p "$RERANKER_PORT:8000" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s --health-timeout=10s --health-retries=20 --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model --trust-remote-code --tensor-parallel-size "$TP_SIZE" \
  --gpu-memory-utilization 0.90 \
  --hf-overrides '{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'

# ── Build and start RAG API ───────────────────────────────────────────────────
echo "Building RAG API image..."
docker build -t "$API_IMAGE" "$SERVICE_DIR"

echo "Starting RAG API (port $API_PORT)..."
docker rm -f rag-api 2>/dev/null || true
docker run -d \
  --name rag-api \
  --network "$NETWORK_NAME" \
  -e EMBEDDING_BASE_URL="http://rag-text-embed:8000/v1" \
  -e EMBEDDING_API_KEY="dummy" \
  -e TEXT_EMBEDDING_MODEL="/model" \
  -e MULTIMODAL_EMBEDDING_BASE_URL="http://rag-multimodal-embed:8000" \
  -e MULTIMODAL_EMBEDDING_API_KEY="dummy" \
  -e MULTIMODAL_EMBEDDING_MODEL="/model" \
  -e RERANKER_BASE_URL="http://rag-reranker:8000" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="/model" \
  -e MILVUS_HOST="milvus-standalone" \
  -e MILVUS_PORT="19530" \
  -v "$DATA_DIR:/app/data" \
  -p "$API_PORT:8093" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8093/health\")" 2>/dev/null && echo ok' \
  --health-interval=10s --health-timeout=5s --health-retries=10 --health-start-period=15s \
  "$API_IMAGE"

echo ""
echo "All services started."
echo "  Text Embedding  -> http://localhost:$TEXT_EMBED_PORT/v1"
echo "  Multimodal Emb  -> http://localhost:$MULTIMODAL_PORT"
echo "  Reranker        -> http://localhost:$RERANKER_PORT"
echo "  RAG API         -> http://localhost:$API_PORT  (docs: /docs)"
