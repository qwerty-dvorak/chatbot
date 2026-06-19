#!/bin/bash
# Start chatbot services with models on RunPod.
# PostgreSQL is expected to be running via db/start.sh.
#
# Usage:
#   bash shell_scripts/start-services-with-runpod.sh
#   bash shell_scripts/start-services-with-runpod.sh --clean
#   bash shell_scripts/start-services-with-runpod.sh --clean --no-rag
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="chatbot_net"
IMAGE_NAME="chatbot-base"
CLEAN=false
NO_RAG=false

for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=true ;;
    --no-rag) NO_RAG=true ;;
    --help|-h)
      echo "Usage: bash shell_scripts/start-services-with-runpod.sh [--clean] [--no-rag]"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $arg"
      echo "Usage: bash shell_scripts/start-services-with-runpod.sh [--clean] [--no-rag]"
      exit 1
      ;;
  esac
done

# Load model endpoints from models/.env.runpod
if [ -f "$ROOT_DIR/models/.env.runpod" ]; then
  export $(grep -v '^#' "$ROOT_DIR/models/.env.runpod" | xargs)
fi

docker rm -f web worker file-server 2>/dev/null || true

if [[ "$NO_RAG" == "true" ]]; then
  RAG_API_ENABLED=false
  RAG_ENABLED=false
  RAG_API_BASE_URL=""
else
  RAG_API_ENABLED="${RAG_API_ENABLED:-true}"
  RAG_ENABLED="${RAG_ENABLED:-true}"
fi

docker network create "$NETWORK_NAME" 2>/dev/null || true
docker volume create media_data 2>/dev/null || true
docker volume create docs_data 2>/dev/null || true

# Gateway IP for cross-container host access (milvus, rag-api publish ports on host)
GATEWAY_IP="$(docker network inspect "$NETWORK_NAME" --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 'localhost')"
MILVUS_HOST="$GATEWAY_IP"
RAG_API_BASE_URL="${RAG_API_BASE_URL:-http://${GATEWAY_IP}:8093}"

echo "Building base image '$IMAGE_NAME' from repo root..."
docker build -t "$IMAGE_NAME" -f "$SERVICE_DIR/Dockerfile" "$ROOT_DIR"

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
echo "  RAG               → $([[ "$NO_RAG" == "true" ]] && echo disabled || echo enabled)"

echo "Starting Django Web App on port 8080..."
docker run -d \
  --name web \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e SECRET_KEY="change-me-in-production" \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5433 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="dummy" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e VISION_MODEL="$VISION_MODEL" \
  -e LORA_ADAPTERS="${LORA_ADAPTERS:-}" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-nvidia/llama-embed-nemotron-8b}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e EMBEDDING_API_KEY="dummy" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="${RERANKER_MODEL:-Qwen/Qwen3-VL-Reranker-2B}" \
  -e MILVUS_HOST="$MILVUS_HOST" \
  -e MILVUS_PORT=19530 \
  -e RAG_API_ENABLED="$RAG_API_ENABLED" \
  -e RAG_API_BASE_URL="$RAG_API_BASE_URL" \
  -e RAG_ENABLED="$RAG_ENABLED" \
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
  -e POSTGRES_PORT=5433 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="dummy" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e LORA_ADAPTERS="${LORA_ADAPTERS:-}" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-nvidia/llama-embed-nemotron-8b}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e EMBEDDING_API_KEY="dummy" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="${RERANKER_MODEL:-Qwen/Qwen3-VL-Reranker-2B}" \
  -e MILVUS_HOST="$MILVUS_HOST" \
  -e MILVUS_PORT=19530 \
  -e RAG_API_ENABLED="$RAG_API_ENABLED" \
  -e RAG_API_BASE_URL="$RAG_API_BASE_URL" \
  -e RAG_ENABLED="$RAG_ENABLED" \
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
echo "  RAG                   → $([[ "$NO_RAG" == "true" ]] && echo disabled || echo enabled)"
echo "  File server         → http://localhost:8888/browse"
echo "  Web app             → http://localhost:8080"
echo ""
echo " Stop with:  docker rm -f web worker file-server"
