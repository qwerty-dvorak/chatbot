#!/bin/bash
# Build and run Django tests inside Docker against a running stack.
#
# Usage:
#   bash tests/chatbot/run-tests.sh                       # all tests
#   bash tests/chatbot/run-tests.sh --keepdb              # reuse test DB
#   bash tests/chatbot/run-tests.sh apps.chat.tests       # specific app
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICE_DIR="$ROOT_DIR/chatbot-service"
NETWORK_NAME="chatbot_net"
TEST_IMAGE="chatbot-test"

POSTGRES_CONTAINER="chatbot-postgres"
POSTGRES_USER="${POSTGRES_USER:-chatbot}"
POSTGRES_DB="${POSTGRES_DB:-chatbot}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}"
EXTRA_ARGS=()

for arg in "$@"; do
  if [[ "$arg" == "--keepdb" ]]; then
    EXTRA_ARGS+=("--keepdb")
  elif [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    echo "Usage: bash tests/chatbot/run-tests.sh [--keepdb] [test_labels...]"
    exit 0
  else
    EXTRA_ARGS+=("$arg")
  fi
done

if ! docker container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: '$POSTGRES_CONTAINER' not running."
  echo "Start the stack first via chatbot-service/run.sh"
  exit 1
fi

echo "Ensuring chatbot user has CREATEDB privilege..."
docker exec "$POSTGRES_CONTAINER" \
  su - postgres -c "psql -c \"ALTER USER ${POSTGRES_USER} CREATEDB;\"" 2>/dev/null || true

echo "Building test image '$TEST_IMAGE'..."
docker build -f "$SCRIPT_DIR/Dockerfile" -t "$TEST_IMAGE" "$ROOT_DIR"

echo ""
echo "Running Django tests..."
echo "  DB: $POSTGRES_USER@$POSTGRES_CONTAINER:5433/$POSTGRES_DB"
echo "  Args: ${EXTRA_ARGS[*]:-(none)}"

docker run --rm \
  --network "$NETWORK_NAME" \
  -e POSTGRES_HOST="$POSTGRES_CONTAINER" \
  -e POSTGRES_PORT=5433 \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e CHAT_BASE_URL="${CHAT_BASE_URL:-http://gemma-inference-server:8000/v1}" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-dummy}" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/google/gemma-4-E4B-it}" \
  "$TEST_IMAGE" \
  "${EXTRA_ARGS[@]}"

EXIT_CODE=$?
[ $EXIT_CODE -eq 0 ] && echo "All tests passed!" || echo "Some tests failed (exit $EXIT_CODE)."
exit $EXIT_CODE
