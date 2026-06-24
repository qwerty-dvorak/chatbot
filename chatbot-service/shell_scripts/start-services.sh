#!/bin/bash
# Start all chatbot services with real Gemma inference (GPU required).
# Infrastructure (PostgreSQL) is expected to be running via db/start.sh.
# Model servers are expected to be running via models/deploy-local-gemma.sh + models/deploy-local-others.sh.
#
# Usage:
#   bash shell_scripts/start-services.sh
#   bash shell_scripts/start-services.sh --clean
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="chatbot_net"
IMAGE_NAME="chatbot-base"

# Load model endpoints from models/.env.local
if [ -f "$ROOT_DIR/models/.env.local" ]; then
  export $(grep -v '^#' "$ROOT_DIR/models/.env.local" | xargs)
fi

DB_NAME="${POSTGRES_DB:-chatbot}"
DB_USER="${POSTGRES_USER:-chatbot}"
DB_PASS="${POSTGRES_PASSWORD:-chatbot}"

EXTERNAL_DB_PORT=5433
EXTERNAL_WEB_PORT=8080

docker rm -f web worker file-server 2>/dev/null || true
docker network create "$NETWORK_NAME" 2>/dev/null || true
docker volume create media_data 2>/dev/null || true
docker volume create docs_data 2>/dev/null || true

# Gateway IP for cross-container host access (milvus publishes ports on host)
GATEWAY_IP="$(docker network inspect "$NETWORK_NAME" --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 'localhost')"

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

echo "Starting Django Web App on host port $EXTERNAL_WEB_PORT..."
docker run -d \
  --name web \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5433 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-dummy}" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e VISION_MODEL="$VISION_MODEL" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-/model}" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RAG_API_ENABLED="${RAG_API_ENABLED:-false}" \
  -e RAG_API_BASE_URL="${RAG_API_BASE_URL:-http://${GATEWAY_IP}:8093}" \
  -e RAG_ENABLED="${RAG_ENABLED:-true}" \
  -e TOOL_CALLS_ENABLED="true" \
  -e MILVUS_HOST="$GATEWAY_IP" \
  -e MILVUS_PORT=19530 \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  -p "$EXTERNAL_WEB_PORT":8000 \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py sync_builtin_tools --settings=config.settings.production && \
         uv run gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2"

echo "Starting Django Worker..."
docker run -d \
  --name worker \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5433 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-dummy}" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e VISION_MODEL="$VISION_MODEL" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-/model}" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RAG_API_ENABLED="${RAG_API_ENABLED:-false}" \
  -e RAG_API_BASE_URL="${RAG_API_BASE_URL:-http://${GATEWAY_IP}:8093}" \
  -e RAG_ENABLED="${RAG_ENABLED:-true}" \
  -e TOOL_CALLS_ENABLED="true" \
  -e MILVUS_HOST="$GATEWAY_IP" \
  -e MILVUS_PORT=19530 \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

echo ""
echo "All services started successfully."
echo "  File server → http://localhost:8888/browse"
echo "  Web app     → http://localhost:$EXTERNAL_WEB_PORT"
