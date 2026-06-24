#!/bin/bash
# Start RAG API container. All model endpoint env vars are injected by
# rag-pipeline/run.sh (which sources models/.env.local or models/.env.runpod).
#
# Usage:
#   bash shell_scripts/start-services.sh
#   bash shell_scripts/start-services.sh --clean
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="rag_net"
API_IMAGE="rag-api"
VOL_DIR="${DOCKER_VOLUME_DIRECTORY:-$SERVICE_DIR/volumes}"
DATA_DIR="$SERVICE_DIR/data"

API_PORT="${API_PORT:-8093}"

docker rm -f rag-api 2>/dev/null || true
docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"

# Gateway IP for cross-container host access (postgres, milvus publish ports on host)
GATEWAY_IP="$(docker network inspect "$NETWORK_NAME" --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 'localhost')"

echo "Building RAG API image..."
docker build -t "$API_IMAGE" "$SERVICE_DIR"

echo "Starting RAG API (port $API_PORT)..."
docker rm -f rag-api 2>/dev/null || true
docker run -d \
  --name rag-api \
  --network "$NETWORK_NAME" \
  -e MILVUS_HOST="$GATEWAY_IP" \
  -e POSTGRES_HOST="$GATEWAY_IP" \
  -e PADDLE_PDX_CACHE_HOME="/app/data/.paddlex" \
  -e CHAT_BASE_URL \
  -e CHAT_API_KEY \
  -e CHAT_MODEL \
  -e VISION_MODEL \
  -e EMBEDDING_BASE_URL \
  -e EMBEDDING_API_KEY \
  -e TEXT_EMBEDDING_MODEL \
  -e TEXT_EMBEDDING_DIM \
  -e MULTIMODAL_EMBEDDING_BASE_URL \
  -e MULTIMODAL_EMBEDDING_API_KEY \
  -e MULTIMODAL_EMBEDDING_MODEL \
  -e MULTIMODAL_EMBEDDING_DIM \
  -e RERANKER_BASE_URL \
  -e RERANKER_API_KEY \
  -e RERANKER_MODEL \
  -e OCR_MODE \
  -e OCR_BASE_URL \
  -e OCR_API_KEY \
  -e OCR_MODEL \
  -e MILVUS_PORT \
  -e POSTGRES_PORT \
  -e POSTGRES_DB \
  -e POSTGRES_USER \
  -e POSTGRES_PASSWORD \
  -e QUERY_ENHANCEMENTS \
  -e HYPOTHETICAL_QUESTIONS_PER_CHUNK \
  -v "$DATA_DIR:/app/data" \
  -p "$API_PORT:8093" \
  "$API_IMAGE"

echo ""
echo "RAG API started (port $API_PORT)."
echo "  RAG API  -> http://localhost:$API_PORT  (docs: /docs)"
