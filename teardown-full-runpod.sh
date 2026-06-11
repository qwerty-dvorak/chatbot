#!/bin/bash
# Tear down the full BARC pipeline.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Tearing down full BARC pipeline..."

# Stop chatbot services
echo "  Stopping chatbot containers..."
docker rm -f web worker file-server 2>/dev/null || true

# Stop RAG pipeline
echo "  Stopping RAG pipeline containers..."
bash "$SCRIPT_DIR/rag-pipeline/start-services.sh" --clean 2>/dev/null || true

# Tear down RunPod pods
echo "  Tearing down RunPod pods..."
bash "$SCRIPT_DIR/chatbot-service/runpod_teardown_chat.sh" 2>/dev/null || true
bash "$SCRIPT_DIR/rag-pipeline/runpod_teardown.sh" 2>/dev/null || true

# Clean up Docker volumes
echo "  Removing Docker volumes..."
docker volume rm docs_data media_data 2>/dev/null || true

echo "Done."
