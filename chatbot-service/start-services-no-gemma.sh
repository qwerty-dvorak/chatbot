#!/bin/bash
# Start all chatbot services EXCEPT the Gemma inference server.
# Useful for frontend / integration testing without real GPUs.
# Point CHAT_BASE_URL at a mock server or an external endpoint instead.
#
# Usage: bash start-services-no-gemma.sh
#        CHAT_BASE_URL=http://localhost:9000/v1 bash start-services-no-gemma.sh
set -e

NETWORK_NAME="chatbot_net"
IMAGE_NAME="chatbot-base"

if [ -f .env.mock ]; then
  export $(grep -v '^#' .env.mock | xargs)
elif [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

DB_NAME=${POSTGRES_DB:-chatbot}
DB_USER=${POSTGRES_USER:-chatbot}
DB_PASS=${POSTGRES_PASSWORD:-chatbot}
EXTERNAL_DB_PORT=5433
EXTERNAL_WEB_PORT=8080

# Default chat endpoint to mock server if not set
CHAT_BASE_URL="${CHAT_BASE_URL:-http://localhost:9000/v1}"

echo "Creating network and volumes..."
docker network create $NETWORK_NAME 2>/dev/null || true
docker volume create postgres_data
docker volume create media_data
docker volume create docs_data

echo "Building base image '$IMAGE_NAME' from local Dockerfile..."
docker build -t $IMAGE_NAME .

echo "Removing any existing containers..."
docker rm -f postgres file-server web worker 2>/dev/null || true

echo "Starting Postgres on host port $EXTERNAL_DB_PORT..."
docker run -d \
  --name postgres \
  --network $NETWORK_NAME \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -v postgres_data:/var/lib/postgresql/data \
  -p $EXTERNAL_DB_PORT:5432 \
  --health-cmd="su - postgres -c '/usr/lib/postgresql/16/bin/pg_isready -d $DB_NAME'" \
  --health-interval=5s \
  --health-timeout=5s \
  --health-retries=10 \
  --health-start-period=10s \
  $IMAGE_NAME postgres-server

echo "Starting File Server..."
docker run -d \
  --name file-server \
  --network $NETWORK_NAME \
  -w /app \
  -e DOCS_ROOT=/data/docs \
  -e FILE_SERVER_PORT="8888" \
  -v "$(pwd)":/app:ro \
  -v docs_data:/data/docs \
  -p 8888:8888 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen('\''http://localhost:8888/browse'\'')" 2>/dev/null && echo ok' \
  --health-interval=5s \
  --health-timeout=3s \
  --health-retries=10 \
  --health-start-period=5s \
  $IMAGE_NAME python3 file_server/server.py

# ── NOTE: Gemma inference server is SKIPPED ──────────────────────────────────
# The CHAT_BASE_URL env var below should point to an alternative endpoint
# (e.g. the rag-pipeline mock server, or an external vLLM instance).

wait_for_health() {
  echo "Waiting for $1 to become healthy..."
  while [ "$(docker inspect -f '{{.State.Health.Status}}' $1 2>/dev/null)" != "healthy" ]; do
    sleep 2
  done
  echo "$1 is healthy!"
}

wait_for_health postgres
wait_for_health file-server

echo "Starting Django Web App on host port $EXTERNAL_WEB_PORT..."
docker run -d \
  --name web \
  --network $NETWORK_NAME \
  --env-file .env.mock \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/mock-chat}" \
  -e VISION_MODEL="${VISION_MODEL:-openai/mock-chat}" \
  -e RAG_ENABLED="${RAG_ENABLED:-false}" \
  -e TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-false}" \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  -p $EXTERNAL_WEB_PORT:8000 \
  $IMAGE_NAME \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py sync_builtin_tools --settings=config.settings.production && \
         uv run gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2"

echo "Starting Django Worker..."
docker run -d \
  --name worker \
  --network $NETWORK_NAME \
  --env-file .env.mock \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/mock-chat}" \
  -e VISION_MODEL="${VISION_MODEL:-openai/mock-chat}" \
  -e RAG_ENABLED="${RAG_ENABLED:-false}" \
  -e TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-false}" \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  $IMAGE_NAME \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

echo ""
echo "All services (except gemma inference) started successfully!"
echo "  Postgres  → localhost:$EXTERNAL_DB_PORT"
echo "  File srv  → http://localhost:8888/browse"
echo "  Web app   → http://localhost:$EXTERNAL_WEB_PORT"
echo "  Chat API  → $CHAT_BASE_URL"
echo ""
echo "Stop with: docker rm -f web worker file-server postgres"
