#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NETWORK_NAME="rag_net"
API_IMAGE="rag-api"
VOL_DIR="${DOCKER_VOLUME_DIRECTORY:-$SCRIPT_DIR/volumes}"
MODEL_DIR="$SCRIPT_DIR/models"
DATA_DIR="$SCRIPT_DIR/data"

# Load .env if present
[ -f "$SCRIPT_DIR/.env" ] && export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)

# Configurable ports (starting from 8090 onwards)
TEXT_EMBED_PORT="${TEXT_EMBED_PORT:-8090}"
MULTIMODAL_PORT="${MULTIMODAL_PORT:-8091}"
RERANKER_PORT="${RERANKER_PORT:-8092}"
API_PORT="${API_PORT:-8093}"
MILVUS_PORT="${MILVUS_PORT:-19530}"
MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"

# GPU device assignments (can be overridden)
TEXT_EMBED_GPU="${TEXT_EMBED_GPU:-0}"
MULTIMODAL_GPU="${MULTIMODAL_GPU:-1}"
RERANKER_GPU="${RERANKER_GPU:-2}"

# Tensor parallel size (set >1 for multi-GPU per model)
TP_SIZE="${TENSOR_PARALLEL_SIZE:-1}"

# ── Optional --clean flag ──────────────────────────────────────────────────────
if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old containers..."
  docker rm -f rag-text-embed rag-multimodal-embed rag-reranker rag-api \
    milvus-standalone milvus-minio milvus-etcd 2>/dev/null || true
fi

# ── Network ───────────────────────────────────────────────────────────────────
echo "Creating network '$NETWORK_NAME'..."
docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"

# ── Volume directories ────────────────────────────────────────────────────────
mkdir -p "$VOL_DIR/etcd" "$VOL_DIR/minio" "$VOL_DIR/milvus"

# ── etcd ─────────────────────────────────────────────────────────────────────
echo "Starting etcd..."
docker run -d \
  --name milvus-etcd \
  --network "$NETWORK_NAME" \
  --network-alias etcd \
  -e ETCD_AUTO_COMPACTION_MODE=revision \
  -e ETCD_AUTO_COMPACTION_RETENTION=1000 \
  -e ETCD_QUOTA_BACKEND_BYTES=4294967296 \
  -e ETCD_SNAPSHOT_COUNT=50000 \
  -v "$VOL_DIR/etcd:/etcd" \
  --health-cmd="wget -q -O - http://127.0.0.1:2379/readyz || etcdctl endpoint health || exit 1" \
  --health-interval=10s \
  --health-timeout=10s \
  --health-retries=5 \
  --health-start-period=30s \
  quay.io/coreos/etcd:v3.7.0-rc.0 \
  etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

# ── MinIO ─────────────────────────────────────────────────────────────────────
echo "Starting minio..."
docker run -d \
  --name milvus-minio \
  --network "$NETWORK_NAME" \
  --network-alias minio \
  -p "$MINIO_PORT:9000" \
  -p "$MINIO_CONSOLE_PORT:9001" \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -v "$VOL_DIR/volumes/minio:/minio_data" \
  --health-cmd="curl -f http://localhost:9000/minio/health/live || exit 1" \
  --health-interval=30s \
  --health-timeout=20s \
  --health-retries=3 \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  minio server /minio_data --console-address ":9001"

# ── Wait for etcd and minio ───────────────────────────────────────────────────
wait_for_health() {
  local name=$1 max_wait=${2:-120}
  echo "Waiting for $name to become healthy (max ${max_wait}s)..."
  local elapsed=0
  while [ "$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null)" != "healthy" ]; do
    sleep 3; elapsed=$((elapsed + 3))
    if [ $elapsed -ge $max_wait ]; then
      echo "ERROR: $name did not become healthy in ${max_wait}s"
      docker logs --tail=20 "$name"
      exit 1
    fi
  done
  echo "$name is healthy!"
}

#wait_for_health milvus-etcd
wait_for_health milvus-minio

# ── Milvus standalone ─────────────────────────────────────────────────────────
echo "Starting Milvus standalone..."
docker run -d \
  --name milvus-standalone \
  --network "$NETWORK_NAME" \
  --network-alias standalone \
  --security-opt seccomp:unconfined \
  -p "$MILVUS_PORT:19530" \
  -p 9091:9091 \
  -e ETCD_ENDPOINTS=etcd:2379 \
  -e MINIO_ADDRESS=minio:9000 \
  -e MQ_TYPE=woodpecker \
  -v "$VOL_DIR/volumes/milvus:/var/lib/milvus" \
  milvusdb/milvus:latest \
  milvus run standalone

echo "Milvus starting (skipping health wait — sleeping 15s instead)..."
sleep 15

# ── Text embedding server ─────────────────────────────────────────────────────
echo "Starting text embedding server (GPU $TEXT_EMBED_GPU, port $TEXT_EMBED_PORT)..."
docker run -d \
  --name rag-text-embed \
  --network "$NETWORK_NAME" \
  --gpus "\"device=$TEXT_EMBED_GPU\"" \
  --shm-size=16g \
  -v "$MODEL_DIR/llama-embed-nemotron-8b:/model" \
  -p "$TEXT_EMBED_PORT:8000" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s \
  --health-timeout=10s \
  --health-retries=20 \
  --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model \
  --trust-remote-code \
  --tensor-parallel-size "$TP_SIZE" \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192

# ── Multimodal embedding server ───────────────────────────────────────────────
echo "Starting multimodal embedding server (GPU $MULTIMODAL_GPU, port $MULTIMODAL_PORT)..."
docker run -d \
  --name rag-multimodal-embed \
  --network "$NETWORK_NAME" \
  --gpus "\"device=$MULTIMODAL_GPU\"" \
  --shm-size=16g \
  -v "$MODEL_DIR/nemotron-colembed-vl-8b-v2:/model" \
  -p "$MULTIMODAL_PORT:8000" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null && echo ok' \
  --health-interval=15s \
  --health-timeout=10s \
  --health-retries=20 \
  --health-start-period=60s \
  vllm/vllm-openai:latest \
  --model /model \
  --trust-remote-code \
  --tensor-parallel-size "$TP_SIZE" \
  --gpu-memory-utilization 0.90 \
  --max-model-len 8192 \
  --limit-mm-per-prompt '{"image": 1, "video": 0}' \
  --skip-mm-profiling \
  --runner pooling

# ── Reranker server ───────────────────────────────────────────────────────────
echo "Starting reranker server (GPU $RERANKER_GPU, port $RERANKER_PORT)..."
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
  --model /model \
  --trust-remote-code \
  --tensor-parallel-size "$TP_SIZE" \
  --gpu-memory-utilization 0.90 \
  --hf-overrides '{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'

# ── Build and start RAG API ───────────────────────────────────────────────────
echo "Building RAG API image..."
docker build -t "$API_IMAGE" "$SCRIPT_DIR"

echo "Starting RAG API (port $API_PORT)..."
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
  -p "$API_PORT:8080" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8080/health\")" 2>/dev/null && echo ok' \
  --health-interval=10s --health-timeout=5s --health-retries=10 --health-start-period=15s \
  "$API_IMAGE"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "All services started."
echo ""
echo "  Milvus          -> localhost:$MILVUS_PORT"
echo "  Text Embedding  -> http://localhost:$TEXT_EMBED_PORT/v1"
  echo "  Multimodal Emb  -> http://localhost:$MULTIMODAL_PORT"
echo "  Reranker        -> http://localhost:$RERANKER_PORT"
echo "  RAG API         -> http://localhost:$API_PORT  (docs: /docs)"
