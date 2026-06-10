#!/bin/bash
# Build and start the mock RAG API server container.
# Provides local substitutes for all RAG pipeline API endpoints.
#
# Usage: bash mock_rag/start.sh [--clean|--build]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOCK_IMAGE="rag-mock-api"
CONTAINER_NAME="rag-mock-api"

if [[ "${1:-}" == "--clean" ]]; then
  echo "Stopping and removing mock RAG API container..."
  docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
  exit 0
fi

if [[ "${1:-}" == "--build" ]] || ! docker image inspect "$MOCK_IMAGE" >/dev/null 2>&1; then
  echo "Building mock RAG API image..."
  docker build -t "$MOCK_IMAGE" "$SCRIPT_DIR"
fi

docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
echo "Starting mock RAG API..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  "$MOCK_IMAGE"

echo ""
echo "Mock RAG API started on:"
echo "  http://localhost:8093  (all RAG pipeline endpoints)"
echo ""
echo "Stop with: docker rm -f $CONTAINER_NAME"
