#!/bin/bash
# Start all chatbot services with mock model server (no GPU required).
# Infrastructure (PostgreSQL, Milvus) expected to be running via db/start.sh and milvus/start.sh.
#
# Usage: bash shell_scripts/start-services-no-gemma.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/.." && pwd)"
NETWORK_NAME="chatbot_net"
IMAGE_NAME="chatbot-base"
MOCK_SERVER_CONTAINER="chatbot-mock-server"

DB_NAME="${POSTGRES_DB:-chatbot}"
DB_USER="${POSTGRES_USER:-chatbot}"
DB_PASS="${POSTGRES_PASSWORD:-chatbot}"
EXTERNAL_WEB_PORT=8080

docker network create "$NETWORK_NAME" 2>/dev/null || true
docker volume create media_data 2>/dev/null || true
docker volume create docs_data 2>/dev/null || true

GATEWAY_IP="$(docker network inspect "$NETWORK_NAME" --format '{{(index .IPAM.Config 0).Gateway}}')"
echo "Gateway IP: $GATEWAY_IP"

echo "Building base image '$IMAGE_NAME'..."
docker build -t "$IMAGE_NAME" "$SERVICE_DIR"

docker rm -f "$MOCK_SERVER_CONTAINER" file-server web worker 2>/dev/null || true

echo "Starting File Server..."
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

CHAT_BASE_URL="http://$MOCK_SERVER_CONTAINER:9000/v1"
EMBEDDING_BASE_URL="http://$MOCK_SERVER_CONTAINER:9001/v1"
RERANKER_BASE_URL="http://$MOCK_SERVER_CONTAINER:9003"
RAG_API_BASE_URL="http://${GATEWAY_IP}:8093"
MILVUS_HOST="${GATEWAY_IP}"

echo "Starting Django Web App on host port $EXTERNAL_WEB_PORT..."
docker run -d \
  --name web \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/mock-chat}" \
  -e VISION_MODEL="${VISION_MODEL:-openai/mock-chat}" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-openai/mock-text-embed}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RAG_API_BASE_URL="$RAG_API_BASE_URL" \
  -e RAG_API_ENABLED=true \
  -e RAG_ENABLED=true \
  -e TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-false}" \
  -e MILVUS_HOST="$MILVUS_HOST" \
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
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/mock-chat}" \
  -e VISION_MODEL="${VISION_MODEL:-openai/mock-chat}" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-openai/mock-text-embed}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RAG_API_BASE_URL="$RAG_API_BASE_URL" \
  -e RAG_API_ENABLED=true \
  -e RAG_ENABLED=true \
  -e TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-false}" \
  -e MILVUS_HOST="$MILVUS_HOST" \
  -e MILVUS_PORT=19530 \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

echo ""
echo "All services (except gemma inference) started!"
echo "  Postgres    → localhost:5433"
echo "  File server → http://localhost:8888/browse"
echo "  Web app     → http://localhost:$EXTERNAL_WEB_PORT"
echo "  RAG API     → $RAG_API_BASE_URL"
echo "  Milvus      → $MILVUS_HOST:19530"
