#!/bin/bash
# Start chatbot services with ALL models on RunPod (chat, embed, reranker).
# RAG pipeline (Milvus + PostgreSQL + rag-api) runs locally in Docker.
#
# Prerequisites:
#   1. RunPod pods must be deployed:
#        export HF_TOKEN=hf_...
#        bash runpod_deploy_chat.sh           # chat LLM
#        cd ../rag-pipeline && bash runpod_deploy.sh  # embed + reranker
#      This creates .runpod_chat_state and .runpod_env with proxy URLs.
#
#   2. RAG pipeline must be running locally:
#        cd ../rag-pipeline && bash start-services.sh
#
#   3. Then start the chatbot:
#        bash start-services-with-runpod.sh
#
# Usage:
#   bash start-services-with-runpod.sh
#   bash start-services-with-runpod.sh --clean   # rebuild image + fresh start
#
# To stop:
#   docker rm -f web worker file-server
#   bash runpod_teardown_chat.sh && cd ../rag-pipeline && bash runpod_teardown.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"
NETWORK_NAME="rag_net"          # shared with RAG pipeline
IMAGE_NAME="chatbot-base"
RAG_API_CONTAINER="rag-api"

# ── Parse flags ───────────────────────────────────────────────────────────────
CLEAN=false
for arg in "$@"; do
  case "$arg" in --clean) CLEAN=true ;; esac
done

# ── Preflight: check RAG pipeline ────────────────────────────────────────────
echo "Checking RAG pipeline infrastructure..."
if ! docker container inspect "$RAG_API_CONTAINER" &>/dev/null; then
  echo "ERROR: '$RAG_API_CONTAINER' not found. Start the RAG pipeline first:"
  echo "  cd ../rag-pipeline && bash start-services.sh"
  exit 1
fi
if ! docker network inspect "$NETWORK_NAME" &>/dev/null; then
  echo "ERROR: Docker network '$NETWORK_NAME' not found."
  exit 1
fi

# Check rag-api health
RAG_API_URL="http://$RAG_API_CONTAINER:8093"
echo "  RAG API: $RAG_API_URL"
if ! curl -sf "$RAG_API_URL/health" >/dev/null 2>&1; then
  echo "  WARNING: RAG API not responding yet."
fi

# ── Read RunPod state ─────────────────────────────────────────────────────────
CHAT_URL=""
if [[ -f "$STATE_FILE" ]]; then
  source "$STATE_FILE"
  echo "Found RunPod chat state: ${CHAT_URL:-<missing>}"
fi
if [[ -z "${CHAT_URL:-}" ]]; then
  echo "ERROR: $STATE_FILE missing or incomplete. Run 'bash runpod_deploy_chat.sh' first."
  exit 1
fi

# RunPod proxy URLs (chat from state file, others from known pod IDs)
CHAT_BASE_URL="${CHAT_URL}/v1"
EMBEDDING_BASE_URL="https://jio9vdwk5an2ot-8000.proxy.runpod.net/v1"
RERANKER_BASE_URL="https://lwq7azeh2cl1xf-8000.proxy.runpod.net"

# ── Build image ───────────────────────────────────────────────────────────────
echo "Building base image '$IMAGE_NAME'..."
$CLEAN && docker build -t "$IMAGE_NAME" .
$CLEAN || docker build -t "$IMAGE_NAME" . 2>&1 | tail -3

# ── Remove old chatbot containers ────────────────────────────────────────────
echo "Removing old chatbot containers..."
docker rm -f file-server web worker 2>/dev/null || true

# ── Start File Server ────────────────────────────────────────────────────────
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

# ── Wait for file-server ─────────────────────────────────────────────────────
echo "Waiting for file-server..."
while [ "$(docker inspect -f '{{.State.Health.Status}}' file-server 2>/dev/null)" != "healthy" ]; do
  sleep 2
done
echo "  file-server is healthy!"

# ── Endpoint configuration ────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Endpoint configuration (all RunPod)"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM          → $CHAT_BASE_URL"
echo "  Text Embed        → $EMBEDDING_BASE_URL"
echo "  Reranker          → $RERANKER_BASE_URL"
echo "  RAG API (local)   → $RAG_API_URL"

# ── Django Web ────────────────────────────────────────────────────────────────
echo "Starting Django Web App on port 8080..."
docker run -d \
  --name web \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e SECRET_KEY="change-me-in-production" \
  -e POSTGRES_HOST=rag-postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="dummy" \
  -e CHAT_MODEL="openai/google/gemma-4-E4B-it" \
  -e VISION_MODEL="openai/google/gemma-4-E4B-it" \
  -e TEXT_EMBEDDING_MODEL="nvidia/llama-embed-nemotron-8b" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e EMBEDDING_API_KEY="dummy" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="Qwen/Qwen3-VL-Reranker-2B" \
  -e MILVUS_HOST=test-milvus \
  -e MILVUS_PORT=19530 \
  -e RAG_API_ENABLED=true \
  -e RAG_API_BASE_URL="$RAG_API_URL" \
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

# ── Django Worker ─────────────────────────────────────────────────────────────
echo "Starting Django Worker..."
docker run -d \
  --name worker \
  --network "$NETWORK_NAME" \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e SECRET_KEY="change-me-in-production" \
  -e POSTGRES_HOST=rag-postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="dummy" \
  -e CHAT_MODEL="openai/google/gemma-4-E4B-it" \
  -e TEXT_EMBEDDING_MODEL="nvidia/llama-embed-nemotron-8b" \
  -e EMBEDDING_BASE_URL="$EMBEDDING_BASE_URL" \
  -e EMBEDDING_API_KEY="dummy" \
  -e RERANKER_BASE_URL="$RERANKER_BASE_URL" \
  -e RERANKER_API_KEY="dummy" \
  -e RERANKER_MODEL="Qwen/Qwen3-VL-Reranker-2B" \
  -e MILVUS_HOST=test-milvus \
  -e MILVUS_PORT=19530 \
  -e RAG_API_ENABLED=true \
  -e RAG_API_BASE_URL="$RAG_API_URL" \
  -e RAG_ENABLED=true \
  -e TOOL_CALLS_ENABLED=true \
  -e CHAT_STREAMING_ENABLED=true \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  "$IMAGE_NAME" \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " All services started!"
echo "══════════════════════════════════════════════════"
echo "  Chat LLM  (RunPod)  → $CHAT_BASE_URL"
echo "  Embed     (RunPod)  → $EMBEDDING_BASE_URL"
echo "  Reranker  (RunPod)  → $RERANKER_BASE_URL"
echo "  RAG API   (local)   → $RAG_API_URL"
echo "  File server         → http://localhost:8888/browse"
echo "  Web app             → http://localhost:8080"
echo ""
echo " Stop with:"
echo "  docker rm -f web worker file-server"
echo "  bash runpod_teardown_chat.sh"
echo "══════════════════════════════════════════════════"
