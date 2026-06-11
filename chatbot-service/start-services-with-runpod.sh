#!/bin/bash
# Start chatbot services with the chat LLM running on RunPod (not locally).
# Postgres, file-server, web, and worker all run in Docker.
# Embed/reranker use the local mock server for offline testing.
#
# Prerequisites:
#   1. Deploy the chat LLM on RunPod:
#        export HF_TOKEN=hf_...
#        bash runpod_deploy_chat.sh
#      This creates .runpod_chat_state with the RunPod proxy URL.
#
#   2. Then start everything else:
#        bash start-services-with-runpod.sh
#
# Usage:
#   bash start-services-with-runpod.sh
#
# To stop:
#   bash runpod_teardown_chat.sh        # stop RunPod pod
#   bash start-services.sh --clean      # stop local containers
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"
NETWORK_NAME="chatbot_net"
IMAGE_NAME="chatbot-base"
MOCK_SERVER_CONTAINER="chatbot-mock-server"

if [ -f .env.mock ]; then
  export $(grep -v '^#' .env.mock | xargs)
elif [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

DB_NAME="${POSTGRES_DB:-chatbot}"
DB_USER="${POSTGRES_USER:-chatbot}"
DB_PASS="${POSTGRES_PASSWORD:-chatbot}"
EXTERNAL_DB_PORT=5433
EXTERNAL_WEB_PORT=8080

# ── Read RunPod state ─────────────────────────────────────────────────────────
if [[ -f "$STATE_FILE" ]]; then
  source "$STATE_FILE"
  echo "Found RunPod chat state: ${CHAT_URL:-<missing>}"
else
  echo "WARNING: $STATE_FILE not found. Run 'bash runpod_deploy_chat.sh' first."
  echo "  Falling back to local mock endpoint on port 9000."
  CHAT_URL="http://localhost:9000"
fi
RUNPOD_CHAT_URL="${RUNPOD_CHAT_URL:-${CHAT_URL:-http://localhost:9000}}"

# ── Infrastructure ─────────────────────────────────────────────────────────
echo "Creating network and volumes..."
docker network create "$NETWORK_NAME" 2>/dev/null || true
docker volume create postgres_data  2>/dev/null || true
docker volume create media_data     2>/dev/null || true
docker volume create docs_data      2>/dev/null || true

echo "Building base image '$IMAGE_NAME'..."
docker build -t "$IMAGE_NAME" .

echo "Removing old containers..."
docker rm -f "$MOCK_SERVER_CONTAINER" postgres file-server web worker 2>/dev/null || true

# Start mock server (provides embed and reranker mocks)
echo "Starting chatbot mock server (embed/reranker)..."
bash "$SCRIPT_DIR/mock_server/start.sh" --build

# Start Postgres
echo "Starting Postgres on host port $EXTERNAL_DB_PORT..."
docker run -d \
  --name postgres \
  --network "$NETWORK_NAME" \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -v postgres_data:/var/lib/postgresql/data \
  -p "$EXTERNAL_DB_PORT":5432 \
  --health-cmd="su - postgres -c '/usr/lib/postgresql/16/bin/pg_isready -d $DB_NAME'" \
  --health-interval=5s \
  --health-timeout=5s \
  --health-retries=10 \
  --health-start-period=10s \
  "$IMAGE_NAME" postgres-server

# Start File Server
echo "Starting File Server..."
docker run -d \
  --name file-server \
  --network "$NETWORK_NAME" \
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
  "$IMAGE_NAME" python3 file_server/server.py

# ── Wait for dependencies ──────────────────────────────────────────────────
wait_for_health() {
  echo "Waiting for $1 to become healthy..."
  while [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" != "healthy" ]; do
    sleep 2
  done
  echo "  $1 is healthy!"
}

wait_for_health postgres
wait_for_health file-server

# ── Endpoint configuration ────────────────────────────────────────────────────
CHAT_BASE_URL="${RUNPOD_CHAT_URL}/v1"
EMBEDDING_BASE_URL="http://$MOCK_SERVER_CONTAINER:9001/v1"
RERANKER_BASE_URL="http://$MOCK_SERVER_CONTAINER:9003"

echo ""
echo "══════════════════════════════════════════════════"
echo " Endpoint configuration"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM    (RunPod) → $CHAT_BASE_URL"
echo "  Embed       (mock)   → $EMBEDDING_BASE_URL"
echo "  Reranker    (mock)   → $RERANKER_BASE_URL"

# ── Web ────────────────────────────────────────────────────────────────────
echo "Starting Django Web App on host port $EXTERNAL_WEB_PORT..."
docker run -d \
  --name web \
  --network "$NETWORK_NAME" \
  --env-file .env.mock \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="local-placeholder" \
  -e CHAT_MODEL="openai/google/gemma-4-E4B-it" \
  -e VISION_MODEL="openai/google/gemma-4-E4B-it" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-openai/mock-text-embed}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RAG_ENABLED=false \
  -e RAG_API_ENABLED=false \
  -e TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-true}" \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  -p "$EXTERNAL_WEB_PORT":8000 \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py sync_builtin_tools --settings=config.settings.production && \
         uv run gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2"

# ── Worker ─────────────────────────────────────────────────────────────────
echo "Starting Django Worker..."
docker run -d \
  --name worker \
  --network "$NETWORK_NAME" \
  --env-file .env.mock \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="local-placeholder" \
  -e CHAT_MODEL="openai/google/gemma-4-E4B-it" \
  -e VISION_MODEL="openai/google/gemma-4-E4B-it" \
  -e TEXT_EMBEDDING_MODEL="${TEXT_EMBEDDING_MODEL:-openai/mock-text-embed}" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RAG_ENABLED=false \
  -e RAG_API_ENABLED=false \
  -e TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-true}" \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " All local services started!"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM  (RunPod)  → $CHAT_BASE_URL"
echo "  Mock server         → $MOCK_SERVER_CONTAINER (embed/reranker)"
echo "  Postgres            → localhost:$EXTERNAL_DB_PORT"
echo "  File server         → http://localhost:8888/browse"
echo "  Web app             → http://localhost:$EXTERNAL_WEB_PORT"
echo ""
echo " Stop with:"
echo "  docker rm -f web worker file-server postgres $MOCK_SERVER_CONTAINER"
echo "  docker network rm $NETWORK_NAME"
echo "  bash runpod_teardown_chat.sh"
echo "══════════════════════════════════════════════════"
