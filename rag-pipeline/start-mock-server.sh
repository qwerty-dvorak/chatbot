#!/bin/bash
# Build and start the mock server Docker container.
# The mock server provides stdlib-only OpenAI-compatible endpoints
# on ports 9000-9003 for local development without real GPUs.
#
# Usage: bash start-mock-server.sh [--clean|--build]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOCK_IMAGE="rag-mock-server"
CONTAINER_NAME="rag-mock-server"

if [[ "${1:-}" == "--clean" ]]; then
  echo "Stopping and removing mock server container..."
  docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
  exit 0
fi

if [[ "${1:-}" == "--build" ]] || ! docker image inspect "$MOCK_IMAGE" >/dev/null 2>&1; then
  echo "Building mock server image..."
  docker build -t "$MOCK_IMAGE" "$SCRIPT_DIR/mock-server"
fi

docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
echo "Starting mock server..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  "$MOCK_IMAGE"

echo ""
echo "Mock server started on ports:"
echo "  Chat completions  → http://localhost:9000/v1/chat/completions"
echo "  Text embeddings   → http://localhost:9001/v1/embeddings"
echo "  Multimodal embed  → http://localhost:9002/v1/embeddings"
echo "  Reranker          → http://localhost:9003/v1/rerank"
echo ""
echo "Stop with: docker rm -f $CONTAINER_NAME"
