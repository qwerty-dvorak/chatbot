#!/bin/bash
# Quick dev reset: nuke all data volumes, restart only db + rag + web.
# Best for iterating on code: schema changes land in fresh PostgreSQL,
# new chunk models re-index into empty Milvus on next full launch.
#
# Usage:
#   bash fresh-start.sh [--mode local|runpod]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# shellcheck source=/dev/null
[[ -f "$SCRIPT_DIR/.env" ]] && source "$SCRIPT_DIR/.env"
MODE="${1:-local}"
MODE="${MODE#--mode=}"
MODE="${MODE,,}"
[[ "$MODE" != "local" && "$MODE" != "runpod" ]] && { echo "Usage: bash fresh-start.sh [--mode local|runpod]"; exit 1; }

echo "╔══════════════════════════════════════════════════╗"
echo "║   FRESH START — Nuke & Relaunch                  ║"
echo "║   Mode: $MODE"
echo "╚══════════════════════════════════════════════════╝"

# --- Kill everything ---
docker rm -f web worker rag-api chatbot-postgres 2>/dev/null || true

# --- Nuke data volumes ---
echo "Nuking PostgreSQL volume..."
docker volume rm postgres_data 2>/dev/null || true

echo "Nuking Milvus volumes (etcd, minio, milvus)..."
docker run --rm -v "$SCRIPT_DIR/milvus/volumes:/v" ubuntu:24.04 bash -c "rm -rf /v/etcd /v/minio /v/milvus"

# --- Source model endpoints ---
ENV_FILE="$SCRIPT_DIR/models/.env.$MODE"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export RAG_API_ENABLED="true"

echo ""
echo "═══ PostgreSQL ═══"
bash "$SCRIPT_DIR/db/start.sh"

echo ""
echo "═══ RAG API ═══"
bash "$SCRIPT_DIR/rag-pipeline/shell_scripts/start-services.sh"

echo ""
echo "═══ Web App + Worker ═══"
bash "$SCRIPT_DIR/chatbot-service/shell_scripts/start-services.sh"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   FRESH STACK IS RUNNING ($MODE)                 ║"
echo "╚══════════════════════════════════════════════════╝"
echo "  Web app:     http://localhost:8080"
echo "  RAG API:     http://localhost:8093/docs"
echo ""
echo "  Milvus, models, and file server are NOT running."
echo "  Start them separately when needed, e.g.:"
echo "    bash milvus/start.sh"
echo ""
echo "  Global seed data is auto-ingested during web container startup."
