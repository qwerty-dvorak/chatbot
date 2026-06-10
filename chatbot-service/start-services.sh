#!/bin/bash
set -e

# --- Configuration & Defaults ---
NETWORK_NAME="chatbot_net"
IMAGE_NAME="chatbot-base"

# Load environment variables if .env exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

DB_NAME=${POSTGRES_DB:-chatbot}
DB_USER=${POSTGRES_USER:-chatbot}
DB_PASS=${POSTGRES_PASSWORD:-chatbot}

# Define alternative external ports to avoid host conflicts
EXTERNAL_DB_PORT=5433
EXTERNAL_WEB_PORT=8080

# --- Step 1: Network & Volumes ---
echo "Creating network and volumes..."
docker network create $NETWORK_NAME 2>/dev/null || true
docker volume create postgres_data
docker volume create media_data
docker volume create docs_data

# --- Step 2: Build the Shared Base Image ---
echo "Building base image '$IMAGE_NAME' from local Dockerfile..."
docker build -t $IMAGE_NAME .

# --- Step 3: Cleanup old containers ---
echo "Removing any existing containers to avoid conflicts..."
docker rm -f postgres file-server gemma-inference-server web worker 2>/dev/null || true

# --- Step 4: Start Independent Services ---

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

echo "Starting Gemma Inference Server..."
docker run -d \
  --name gemma-inference-server \
  --network $NETWORK_NAME \
  --shm-size="16gb" \
  --gpus all \
  -e NVIDIA_VISIBLE_DEVICES=0,1 \
  -v "$(cd "$(dirname "$0")/../.." && pwd)/models/gemma-4-26B-A4B-it:/gemma4-26B-A4b" \
  -p 8430:8000 \
  --health-cmd='python3 -c "import urllib.request; urllib.request.urlopen('\''http://localhost:8000/v1/models'\'')" 2>/dev/null && echo ok' \
  --health-interval=10s \
  --health-timeout=5s \
  --health-retries=15 \
  --health-start-period=30s \
  vllm/vllm-openai:latest \
  --model /gemma4-26B-A4b \
  --tensor-parallel-size 2 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.85 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4

# --- Step 5: Wait for Dependencies ---
wait_for_health() {
  echo "Waiting for $1 to become healthy..."
  while [ "$(docker inspect -f '{{.State.Health.Status}}' $1 2>/dev/null)" != "healthy" ]; do
    sleep 2
  done
  echo "$1 is healthy!"
}

wait_for_health postgres
wait_for_health file-server

# --- Step 6: Start Dependent Services ---

echo "Starting Django Web App on host port $EXTERNAL_WEB_PORT..."
docker run -d \
  --name web \
  --network $NETWORK_NAME \
  --env-file .env \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL=http://gemma-inference-server:8000/v1 \
  -e CHAT_MODEL=openai//gemma4-26B-A4b \
  -e VISION_MODEL=openai//gemma4-26B-A4b \
  -e RAG_ENABLED="false" \
  -e TOOL_CALLS_ENABLED="true" \
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
  --env-file .env \
  -e DJANGO_SETTINGS_MODULE=config.settings.production \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e DOCS_ROOT=/data/docs \
  -e CHAT_BASE_URL=http://gemma-inference-server:8000/v1 \
  -e CHAT_MODEL=openai//gemma4-26B-A4b \
  -e VISION_MODEL=openai//gemma4-26B-A4b \
  -e RAG_ENABLED="false" \
  -e TOOL_CALLS_ENABLED="true" \
  -v media_data:/app/media \
  -v docs_data:/data/docs \
  $IMAGE_NAME \
  sh -c "uv run python manage.py migrate --settings=config.settings.production && \
         uv run python manage.py run_ingestion_worker --settings=config.settings.production"

echo "All services have been started successfully."
