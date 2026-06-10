#!/bin/bash
# Start all chatbot services EXCEPT the Gemma inference server.
# Uses the chatbot mock server container for chat/embed/reranker models,
# and the host's rag-pipeline mock for RAG API and Milvus (via gateway).
#
# Before running, start the rag-pipeline mock services:
#   bash ../rag-pipeline/start_mock_all.sh
#
# Usage: bash start-services-no-gemma.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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

MOCK_SERVER_CONTAINER="chatbot-mock-server"

echo "Creating network and volumes..."
docker network create $NETWORK_NAME 2>/dev/null || true
docker volume create postgres_data
docker volume create media_data
docker volume create docs_data

# Get Docker gateway IP for reaching host-network services (rag-pipeline mock servers, Milvus)
GATEWAY_IP="$(docker network inspect $NETWORK_NAME --format '{{(index .IPAM.Config 0).Gateway}}')"
echo "Gateway IP for $NETWORK_NAME: $GATEWAY_IP"

echo "Building base image '$IMAGE_NAME' from local Dockerfile..."
docker build -t $IMAGE_NAME .

echo "Removing any existing containers..."
docker rm -f "$MOCK_SERVER_CONTAINER" postgres file-server web worker 2>/dev/null || true

# Start chatbot mock server (chat, embed, reranker) on chatbot_net
echo "Starting chatbot mock server..."
bash "$SCRIPT_DIR/mock_server/start.sh" --build

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
# The chatbot mock server container provides LiteLLM-compatible endpoints on
# chatbot_net. RAG pipeline mock servers and Milvus are reached via host gateway.

wait_for_health() {
  echo "Waiting for $1 to become healthy..."
  while [ "$(docker inspect -f '{{.State.Health.Status}}' $1 2>/dev/null)" != "healthy" ]; do
    sleep 2
  done
  echo "$1 is healthy!"
}

wait_for_health postgres
wait_for_health file-server

# Override model endpoint URLs to use the chatbot mock server container
# (on chatbot_net, reachable by container name).
# RAG API and Milvus use the Docker gateway IP to reach host-network services
# (rag-pipeline mock server on port 8093, Milvus on port 19530).
CHAT_BASE_URL="http://$MOCK_SERVER_CONTAINER:9000/v1"
EMBEDDING_BASE_URL="http://$MOCK_SERVER_CONTAINER:9001/v1"
RERANKER_BASE_URL="http://$MOCK_SERVER_CONTAINER:9003"
RAG_API_BASE_URL="http://${GATEWAY_IP}:8093"
MILVUS_HOST="${GATEWAY_IP}"

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
  $IMAGE_NAME \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

echo ""
echo "All services (except gemma inference) started successfully!"
echo "  Chat mock   → $MOCK_SERVER_CONTAINER (chatbot_net)"
echo "  Postgres    → localhost:$EXTERNAL_DB_PORT"
echo "  File srv    → http://localhost:8888/browse"
echo "  Web app     → http://localhost:$EXTERNAL_WEB_PORT"
echo "  Chat API    → $CHAT_BASE_URL"
echo "  Embed API   → $EMBEDDING_BASE_URL"
echo "  Reranker    → $RERANKER_BASE_URL"
echo "  RAG API     → $RAG_API_BASE_URL"
echo "  Milvus      → $MILVUS_HOST:19530"
echo ""
echo "Stop with: docker rm -f web worker file-server postgres $MOCK_SERVER_CONTAINER"
