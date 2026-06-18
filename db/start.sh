#!/bin/bash
# Start PostgreSQL 16 on port 5433 (shared between chatbot-service and rag-pipeline).
# Runs inside the chatbot-base image (Ubuntu 24.04 + postgresql-16) with the
# db/entrypoint.sh handling initialization and foreground execution.
# Data is persisted in a Docker volume named 'postgres_data'.
#
# Usage: bash start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/chatbot-service"
export $(grep -v '^#' "$SCRIPT_DIR/../models/.env.local" 2>/dev/null | xargs || true)

DB_NAME="${POSTGRES_DB:-chatbot}"
DB_USER="${POSTGRES_USER:-chatbot}"
DB_PASS="${POSTGRES_PASSWORD:-chatbot}"
EXTERNAL_PORT="${POSTGRES_PORT:-5433}"
IMAGE_NAME="chatbot-base"
NETWORK_NAME="chatbot_net"

echo "Creating volume..."
docker volume create postgres_data 2>/dev/null || true
docker network create "$NETWORK_NAME" 2>/dev/null || true

echo "Removing old container..."
docker rm -f chatbot-postgres 2>/dev/null || true

echo "Building base image '$IMAGE_NAME' from repo root..."
docker build -t "$IMAGE_NAME" -f "$SERVICE_DIR/Dockerfile" "$ROOT_DIR"

echo "Starting PostgreSQL on port $EXTERNAL_PORT..."
docker run -d \
  --name chatbot-postgres \
  --network "$NETWORK_NAME" \
  -e POSTGRES_DB="$DB_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASS" \
  -e POSTGRES_PORT="$EXTERNAL_PORT" \
  -v postgres_data:/var/lib/postgresql/data \
  -p "$EXTERNAL_PORT":"$EXTERNAL_PORT" \
  --health-cmd="runuser -u postgres -- /usr/lib/postgresql/16/bin/pg_isready -p $EXTERNAL_PORT -d $DB_NAME" \
  --health-interval=5s \
  --health-timeout=5s \
  --health-retries=10 \
  "$IMAGE_NAME" postgres-server

echo "Waiting for PostgreSQL to become healthy..."
while [ "$(docker inspect -f '{{.State.Health.Status}}' chatbot-postgres 2>/dev/null)" != "healthy" ]; do
  sleep 2
done

echo "PostgreSQL ready at localhost:$EXTERNAL_PORT"
