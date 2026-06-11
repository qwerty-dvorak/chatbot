#!/bin/bash
# Start the RAG pipeline stack with all ML models on RunPod.
# - Milvus, etcd, MinIO, PostgreSQL, rag-api run locally in Docker
# - Text Embed, Multimodal Embed, Reranker, Chat LLM all run on RunPod
#
# This is the RAG-only counterpart to ../chatbot-service/start-services-with-runpod.sh.
# Use together for the full BARC pipeline.
#
# Prerequisites:
#   RunPod pods must be deployed first:
#     bash runpod_deploy.sh
#
# Usage:
#   bash start-services-runpod.sh
#   bash start-services-runpod.sh --clean   # full teardown + restart
#
# Environment overrides (see .env.example):
#   MILVUS_HOST, MILVUS_PORT, API_PORT, POSTGRES_HOST, etc.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NETWORK_NAME="rag_net"
API_IMAGE="rag-api"
VOL_DIR="${DOCKER_VOLUME_DIRECTORY:-$SCRIPT_DIR/volumes}"
DATA_DIR="$SCRIPT_DIR/data"

# Load .env (which has RunPod endpoint URLs)
[ -f "$SCRIPT_DIR/.env" ] && export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)

# Configurable ports
API_PORT="${API_PORT:-8093}"
MILVUS_PORT="${MILVUS_PORT:-19530}"
MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"

# ── Optional --clean flag ──────────────────────────────────────────────────────
if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old containers..."
  docker rm -f rag-api milvus-standalone milvus-minio milvus-etcd \
    rag-postgres 2>/dev/null || true
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
  --health-interval=10s --health-timeout=10s --health-retries=5 --health-start-period=30s \
  quay.io/coreos/etcd:v3.7.0-rc.0 \
  etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

# ── MinIO ─────────────────────────────────────────────────────────────────────
echo "Starting minio..."
docker run -d \
  --name milvus-minio \
  --network "$NETWORK_NAME" \
  --network-alias minio \
  -p "$MINIO_PORT:9000" -p "$MINIO_CONSOLE_PORT:9001" \
  -e MINIO_ACCESS_KEY=minioadmin -e MINIO_SECRET_KEY=minioadmin \
  -v "$VOL_DIR/volumes/minio:/minio_data" \
  --health-cmd="curl -f http://localhost:9000/minio/health/live || exit 1" \
  --health-interval=30s --health-timeout=20s --health-retries=3 \
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

wait_for_health milvus-minio

# ── Milvus standalone ─────────────────────────────────────────────────────────
echo "Starting Milvus standalone..."
docker run -d \
  --name milvus-standalone \
  --network "$NETWORK_NAME" \
  --network-alias standalone \
  --security-opt seccomp:unconfined \
  -p "$MILVUS_PORT:19530" -p 9091:9091 \
  -e ETCD_ENDPOINTS=etcd:2379 \
  -e MINIO_ADDRESS=minio:9000 \
  -e MQ_TYPE=woodpecker \
  -v "$VOL_DIR/volumes/milvus:/var/lib/milvus" \
  milvusdb/milvus:latest \
  milvus run standalone

echo "Milvus starting (sleeping 15s)..."
sleep 15

# ── Start PostgreSQL ──────────────────────────────────────────────────────────
echo "Starting PostgreSQL..."
if ! docker container inspect rag-postgres &>/dev/null; then
  docker run -d \
    --name rag-postgres \
    --network "$NETWORK_NAME" \
    -e POSTGRES_DB="${POSTGRES_DB:-chatbot}" \
    -e POSTGRES_USER="${POSTGRES_USER:-chatbot}" \
    -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}" \
    -v postgres_data:/var/lib/postgresql/data \
    --health-cmd="su - postgres -c '/usr/lib/postgresql/16/bin/pg_isready -d ${POSTGRES_DB:-chatbot}'" \
    --health-interval=5s --health-timeout=5s --health-retries=10 --health-start-period=10s \
    postgres:16
else
  echo "  rag-postgres already exists, reusing..."
fi

# ── Wait for PostgreSQL ──────────────────────────────────────────────────────
wait_for_postgres() {
  echo "Waiting for PostgreSQL..."
  local elapsed=0
  while [ "$(docker inspect -f '{{.State.Health.Status}}' rag-postgres 2>/dev/null)" != "healthy" ]; do
    sleep 3; elapsed=$((elapsed + 3))
    if [ $elapsed -ge 60 ]; then
      echo "ERROR: PostgreSQL did not become healthy"
      docker logs --tail=20 rag-postgres
      exit 1
    fi
  done
  echo "PostgreSQL is healthy!"
}
wait_for_postgres

# ── Build and start RAG API ───────────────────────────────────────────────────
echo "Building RAG API image..."
docker build -t "$API_IMAGE" "$SCRIPT_DIR"

echo "Starting RAG API (port $API_PORT)..."
docker run -d \
  --name rag-api \
  --network "$NETWORK_NAME" \
  -e CHAT_BASE_URL="${CHAT_BASE_URL:-}" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-dummy}" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/google/gemma-4-E4B-it}" \
  -e EMBEDDING_BASE_URL="${EMBEDDING_BASE_URL:-}" \
  -e EMBEDDING_API_KEY="${EMBEDDING_API_KEY:-dummy}" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-nvidia/llama-embed-nemotron-8b}" \
  -e TEXT_EMBEDDING_DIM="${TEXT_EMBEDDING_DIM:-4096}" \
  -e MULTIMODAL_EMBEDDING_BASE_URL="${MULTIMODAL_EMBEDDING_BASE_URL:-}" \
  -e MULTIMODAL_EMBEDDING_API_KEY="${MULTIMODAL_EMBEDDING_API_KEY:-dummy}" \
  -e MULTIMODAL_EMBEDDING_MODEL="${MULTIMODAL_EMBEDDING_MODEL:-nvidia/nemotron-colembed-vl-8b-v2}" \
  -e MULTIMODAL_EMBEDDING_DIM="${MULTIMODAL_EMBEDDING_DIM:-4096}" \
  -e RERANKER_BASE_URL="${RERANKER_BASE_URL:-}" \
  -e RERANKER_API_KEY="${RERANKER_API_KEY:-dummy}" \
  -e RERANKER_MODEL="${RERANKER_MODEL:-Qwen/Qwen3-VL-Reranker-2B}" \
  -e MILVUS_HOST="milvus-standalone" \
  -e MILVUS_PORT="19530" \
  -e POSTGRES_HOST="rag-postgres" \
  -e POSTGRES_PORT="5432" \
  -e POSTGRES_DB="${POSTGRES_DB:-chatbot}" \
  -e POSTGRES_USER="${POSTGRES_USER:-chatbot}" \
  -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}" \
  -e QUERY_ENHANCEMENTS="${QUERY_ENHANCEMENTS:-hyde}" \
  -e HIPOTETICAL_QUESTIONS_PER_CHUNK="${HIPOTETICAL_QUESTIONS_PER_CHUNK:-0}" \
  -v "$DATA_DIR:/app/data" \
  -p "$API_PORT:8093" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8093/health\")" 2>/dev/null && echo ok' \
  --health-interval=10s --health-timeout=5s --health-retries=10 --health-start-period=15s \
  "$API_IMAGE"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "All RAG pipeline services started (ML models on RunPod)."
echo ""
echo "  Milvus   -> localhost:$MILVUS_PORT"
echo "  Postgres -> localhost:5432"
echo "  RAG API  -> http://localhost:$API_PORT  (docs: /docs)"
echo ""
echo " RunPod endpoints (from .env):"
echo "  Chat LLM:          ${CHAT_BASE_URL:-<not set>}"
echo "  Text Embed:        ${EMBEDDING_BASE_URL:-<not set>}"
echo "  Multimodal Embed:  ${MULTIMODAL_EMBEDDING_BASE_URL:-<not set>}"
echo "  Reranker:          ${RERANKER_BASE_URL:-<not set>}"
echo ""
echo " Start the chatbot service separately:"
echo "  cd ../chatbot-service && bash start-services-with-runpod.sh"
