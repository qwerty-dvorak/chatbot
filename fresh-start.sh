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
MODE="${1:-${MODE:-local}}"
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

echo "Nuking RAG pipeline data (SQLite jobs, uploads)..."
docker run --rm -v "$SCRIPT_DIR/rag-pipeline/data:/data" ubuntu:24.04 bash -c "rm -rf /data/ingestion /data/.paddlex 2>/dev/null; mkdir -p /data/ingestion"

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
echo "═══ Milvus (etcd + minio + milvus-standalone) ═══"
docker rm -f milvus-etcd milvus-minio milvus-standalone 2>/dev/null || true
NETWORK_NAME="milvus_net"
docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"
VOL_DIR="$SCRIPT_DIR/milvus/volumes"
mkdir -p "$VOL_DIR/etcd" "$VOL_DIR/minio" "$VOL_DIR/milvus"
docker run -d --name milvus-etcd --network "$NETWORK_NAME" --network-alias etcd \
  -e ETCD_AUTO_COMPACTION_MODE=revision -e ETCD_AUTO_COMPACTION_RETENTION=1000 \
  -e ETCD_QUOTA_BACKEND_BYTES=4294967296 -e ETCD_SNAPSHOT_COUNT=50000 \
  -v "$VOL_DIR/etcd:/etcd" \
  quay.io/coreos/etcd:v3.7.0-rc.0 \
  etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd
docker run -d --name milvus-minio --network "$NETWORK_NAME" --network-alias minio \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ACCESS_KEY=minioadmin -e MINIO_SECRET_KEY=minioadmin \
  -v "$VOL_DIR/minio:/minio_data" \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  minio server /minio_data --console-address ":9001"
docker run -d --name milvus-standalone --network "$NETWORK_NAME" --network-alias standalone \
  --security-opt seccomp:unconfined \
  -p 19530:19530 -p 9091:9091 \
  -e ETCD_ENDPOINTS=etcd:2379 -e MINIO_ADDRESS=minio:9000 -e MQ_TYPE=woodpecker \
  -v "$VOL_DIR/milvus:/var/lib/milvus" \
  milvusdb/milvus:latest \
  milvus run standalone

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
echo "  Models and file server are NOT running."
echo "  Start them separately when needed, e.g.:"
echo "    bash models/deploy-local-gemma.sh"
echo ""
echo "  Global seed data is auto-ingested during web container startup."
