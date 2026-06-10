#!/bin/bash
# Build and start the chatbot mock server as a Docker container.
# Provides mock chat completions (9000), embeddings (9001), reranker (9003).
#
# Usage: bash mock_server/start.sh [--clean|--build]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOCK_IMAGE="chatbot-mock-server"
CONTAINER_NAME="chatbot-mock-server"
NETWORK="${NETWORK:-chatbot_net}"

if [[ "${1:-}" == "--clean" ]]; then
  echo "Stopping and removing chatbot mock server..."
  docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
  exit 0
fi

if [[ "${1:-}" == "--build" ]] || ! docker image inspect "$MOCK_IMAGE" >/dev/null 2>&1; then
  echo "Building chatbot mock server image..."
  docker build -t "$MOCK_IMAGE" "$SCRIPT_DIR"
fi

docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
echo "Starting chatbot mock server on network '$NETWORK'..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --network "$NETWORK" \
  "$MOCK_IMAGE"

echo ""
echo "Chatbot mock server started:"
echo "  http://$CONTAINER_NAME:9000/v1/chat/completions"
echo "  http://$CONTAINER_NAME:9001/v1/embeddings"
echo "  http://$CONTAINER_NAME:9003/score"
echo ""
echo "Stop with: docker rm -f $CONTAINER_NAME"
