#!/bin/bash
# Start chatbot services with ALL models on RunPod.
# Infrastructure (PostgreSQL, Milvus) expected to be running via db/ and milvus/.
# RAG pipeline expected to be running via rag-pipeline/shell_scripts/.
#
# Usage:
#   bash shell_scripts/start-services-with-runpod.sh
#   bash shell_scripts/start-services-with-runpod.sh --clean
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="rag_net"
IMAGE_NAME="chatbot-base"

# Load model endpoints from models/.env.runpod
if [ -f "$ROOT_DIR/models/.env.runpod" ]; then
  export $(grep -v '^#' "$ROOT_DIR/models/.env.runpod" | xargs)
fi

if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old containers..."
  docker rm -f web worker file-server 2>/dev/null || true
fi

echo "Checking RAG pipeline infrastructure..."
if ! docker network inspect "$NETWORK_NAME" &>/dev/null; then
  echo "ERROR: Network '$NETWORK_NAME' not found. Start RAG pipeline services first."
  exit 1
fi

echo "Building base image '$IMAGE_NAME'..."
docker build -t "$IMAGE_NAME" "$SERVICE_DIR"

echo "Starting File Server..."
docker rm -f file-server 2>/dev/null || true
docker run -d \
  --name file-server \
  --network "$NETWORK_NAME" \
  -w /app \
  -e DOCS_ROOT=/data/docs \
  -e FILE_SERVER_PORT="8888" \
  -v "$SERVICE_DIR":/app:ro \
  -v docs_data:/data/docs \
  -p 8888:8888 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8888/browse\")" 2>/dev/null && echo ok' \
  --health-interval=5s --health-timeout=3s --health-retries=10 --health-start-period=5s \
  "$IMAGE_NAME" python3 file_server/server.py

echo "Waiting for file-server..."
while [ "$(docker inspect -f '{{.State.Health.Status}}' file-server 2>/dev/null)" != "healthy" ]; do
  sleep 2
done

echo ""
echo "Endpoint configuration (all RunPod):"
echo "  Chat LLM          → $CHAT_BASE_URL"
echo "  Text Embed        → $EMBEDDING_BASE_URL"
echo "  Reranker          → $RERANKER_BASE_URL"
echo "  RAG API (local)   → http://rag-api:8093"

echo "Starting Django Web App on port 8080..."
docker run -d \
  --name web \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e SECRET_KEY="change-me-in-production" \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="dummy" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e VISION_MODEL="$VISION_MODEL" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-nvidia/llama-embed-nemotron-8b}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e EMBEDDING_API_KEY="dummy" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="${RERANKER_MODEL:-Qwen/Qwen3-VL-Reranker-2B}" \
  -e MILVUS_HOST=milvus-standalone \
  -e MILVUS_PORT=19530 \
  -e RAG_API_ENABLED=true \
  -e RAG_API_BASE_URL="http://rag-api:8093" \
  -e RAG_ENABLED=true \
  -e RAG_TOP_K=5 \
  -e RAG_MIN_SIMILARITY=0.45 \
  -e TOOL_CALLS_ENABLED=true \
  -e CHAT_STREAMING_ENABLED=true \
  -e CHAT_CONTEXT_MAX_TOKENS=32000 \
  -e CHAT_RESPONSE_MAX_TOKENS=2048 \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  -p 8080:8000 \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py sync_builtin_tools --settings=config.settings.production && \
         uv run gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2"

echo "Starting Django Worker..."
docker run -d \
  --name worker \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e SECRET_KEY="change-me-in-production" \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="dummy" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-nvidia/llama-embed-nemotron-8b}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e EMBEDDING_API_KEY="dummy" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="${RERANKER_MODEL:-Qwen/Qwen3-VL-Reranker-2B}" \
  -e MILVUS_HOST=milvus-standalone \
  -e MILVUS_PORT=19530 \
  -e RAG_API_ENABLED=true \
  -e RAG_API_BASE_URL="http://rag-api:8093" \
  -e RAG_ENABLED=true \
  -e TOOL_CALLS_ENABLED=true \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

echo ""
echo "All services started!"
echo "  Chat LLM  (RunPod)  → $CHAT_BASE_URL"
echo "  Embed     (RunPod)  → $EMBEDDING_BASE_URL"
echo "  Reranker  (RunPod)  → $RERANKER_BASE_URL"
echo "  RAG API   (local)   → http://rag-api:8093"
echo "  File server         → http://localhost:8888/browse"
echo "  Web app             → http://localhost:8080"
echo ""
echo " Stop with:  docker rm -f web worker file-server"
