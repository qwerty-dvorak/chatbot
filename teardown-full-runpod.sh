#!/bin/bash
# Tear down the full BARC pipeline.
#
# Usage: bash teardown-full-runpod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Tearing down full BARC pipeline..."

echo "  Stopping chatbot containers..."
docker rm -f web worker file-server 2>/dev/null || true

echo "  Stopping RAG pipeline containers..."
docker rm -f rag-api 2>/dev/null || true

echo "  Tearing down RunPod pods..."
bash "$SCRIPT_DIR/models/teardown-runpod.sh" 2>/dev/null || true

echo "  Removing Docker volumes..."
docker volume rm docs_data media_data 2>/dev/null || true

echo "Done."
