#!/bin/bash
# Tear down the full BARC pipeline (reverse of start-full-runpod.sh).
# Stops infrastructure, services, RunPod pods, and cleans volumes.
#
# Usage: bash teardown-full-runpod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Tearing down full BARC pipeline..."

echo "  Stopping chatbot containers..."
docker rm -f web worker file-server 2>/dev/null || true

echo "  Stopping RAG pipeline containers..."
docker rm -f rag-api 2>/dev/null || true

echo "  Stopping infrastructure..."
bash "$SCRIPT_DIR/milvus/stop.sh" 2>/dev/null || true
bash "$SCRIPT_DIR/db/stop.sh" 2>/dev/null || true

echo "  Tearing down RunPod pods..."
bash "$SCRIPT_DIR/models/teardown-runpod.sh" 2>/dev/null || true

echo "  Removing Docker volumes..."
docker volume rm docs_data media_data postgres_data 2>/dev/null || true

echo "Done."
