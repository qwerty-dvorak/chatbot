#!/bin/bash
# Start RAG pipeline with ML models on RunPod (Milvus, PostgreSQL, RAG API locally).
#
# Usage:
#   bash shell_scripts/start-services-runpod.sh
#   bash shell_scripts/start-services-runpod.sh --clean
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="rag_net"
API_IMAGE="rag-api"
VOL_DIR="${DOCKER_VOLUME_DIRECTORY:-$SERVICE_DIR/volumes}"
DATA_DIR="$SERVICE_DIR/data"

# Load RunPod endpoints from models/.env.runpod
[ -f "$ROOT_DIR/models/.env.runpod" ] && export $(grep -v '^#' "$ROOT_DIR/models/.env.runpod" | xargs)

API_PORT="${API_PORT:-8093}"
MILVUS_PORT="${MILVUS_PORT:-19530}"
MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"

if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old containers..."
  docker rm -f rag-api 2>/dev/null || true
fi

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"

echo "Building RAG API image..."
docker build -t "$API_IMAGE" "$SERVICE_DIR"

echo "Starting RAG API (port $API_PORT)..."
docker rm -f rag-api 2>/dev/null || true
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
  -e POSTGRES_HOST="chatbot-postgres" \
  -e POSTGRES_PORT="5432" \
  -e POSTGRES_DB="${POSTGRES_DB:-chatbot}" \
  -e POSTGRES_USER="${POSTGRES_USER:-chatbot}" \
  -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}" \
  -e QUERY_ENHANCEMENTS="${QUERY_ENHANCEMENTS:-hyde}" \
  -e HYPOTHETICAL_QUESTIONS_PER_CHUNK="${HYPOTHETICAL_QUESTIONS_PER_CHUNK:-0}" \
  -v "$DATA_DIR:/app/data" \
  -p "$API_PORT:8093" \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8093/health\")" 2>/dev/null && echo ok' \
  --health-interval=10s --health-timeout=5s --health-retries=10 --health-start-period=15s \
  "$API_IMAGE"

echo ""
echo "RAG pipeline services started (ML models on RunPod)."
echo "  RAG API  -> http://localhost:$API_PORT  (docs: /docs)"
echo ""
echo " RunPod endpoints (from models/.env.runpod):"
echo "  Text Embed:       ${EMBEDDING_BASE_URL:-<not set>}"
echo "  Multimodal Embed: ${MULTIMODAL_EMBEDDING_BASE_URL:-<not set>}"
echo "  Reranker:         ${RERANKER_BASE_URL:-<not set>}"
