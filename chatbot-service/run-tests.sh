#!/bin/bash
# Build and run the test suite inside Docker against a running stack.
#
# Requires one of these to be running first:
#   bash start-services.sh
#   bash start-services-no-gemma.sh
#   bash start-services-with-runpod.sh
#
# Usage:
#   bash run-tests.sh                           # all tests
#   bash run-tests.sh apps.chat.tests           # specific app
#   bash run-tests.sh --keepdb                  # reuse existing test DB
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NETWORK_NAME="chatbot_net"
TEST_IMAGE="chatbot-test"

POSTGRES_CONTAINER="postgres"
POSTGRES_USER="${POSTGRES_USER:-chatbot}"
POSTGRES_DB="${POSTGRES_DB:-chatbot}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}"

EXTRA_ARGS=()

# ── Parse flags ────────────────────────────────────────────────────────────
for arg in "$@"; do
  if [[ "$arg" == "--keepdb" ]]; then
    EXTRA_ARGS+=("--keepdb")
  elif [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    echo "Usage: bash run-tests.sh [--keepdb] [test_labels...]"
    echo ""
    echo "  --keepdb     Preserve test database between runs (faster)"
    echo "  test_labels  Django test labels (e.g. apps.chat.tests)"
    echo ""
    echo "Examples:"
    echo "  bash run-tests.sh"
    echo "  bash run-tests.sh --keepdb"
    echo "  bash run-tests.sh apps.chat.tests.test_chat_api"
    exit 0
  else
    EXTRA_ARGS+=("$arg")
  fi
done

# ── Verify stack is running ────────────────────────────────────────────────
if ! docker container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: '$POSTGRES_CONTAINER' container is not running."
  echo "Start the stack first:"
  echo "  bash start-services.sh"
  echo "  bash start-services-no-gemma.sh"
  echo "  bash start-services-with-runpod.sh"
  exit 1
fi

POSTGRES_HOST="$POSTGRES_CONTAINER"
POSTGRES_PORT=5432

# Check postgres health
echo "Checking postgres health..."
for i in $(seq 1 15); do
  if docker exec "$POSTGRES_CONTAINER" pg_isready -q 2>/dev/null; then
    echo "  postgres is healthy."
    break
  fi
  sleep 2
  [[ $i -eq 15 ]] && { echo "ERROR: postgres not ready after 30s"; exit 1; }
done

# ── Grant CREATEDB so test runner can create/drop test databases ────────────
echo "Ensuring chatbot user has CREATEDB privilege..."
docker exec "$POSTGRES_CONTAINER" \
  su - postgres -c "psql -c \"ALTER USER ${POSTGRES_USER} CREATEDB;\"" 2>/dev/null || true

# ── Build test image ───────────────────────────────────────────────────────
echo "Building test image '$TEST_IMAGE'..."
docker build -f "$SCRIPT_DIR/Dockerfile.test" -t "$TEST_IMAGE" "$SCRIPT_DIR"

# ── Run tests ──────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Running Django tests..."
echo " Network: $NETWORK_NAME"
echo " DB:      $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
echo " Args:    ${EXTRA_ARGS[*]:-(none)}"
echo "══════════════════════════════════════════════════"
echo ""

docker run --rm \
  --network "$NETWORK_NAME" \
  -e POSTGRES_HOST="$POSTGRES_HOST" \
  -e POSTGRES_PORT="$POSTGRES_PORT" \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e CHAT_BASE_URL="${CHAT_BASE_URL:-https://0rmd1w8xit8ht5-8000.proxy.runpod.net/v1}" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-local-placeholder}" \
  -e CHAT_MODEL="${CHAT_MODEL:-openai/google/gemma-4-E4B-it}" \
  "$TEST_IMAGE" \
  "${EXTRA_ARGS[@]}"

# ── Result ─────────────────────────────────────────────────────────────────
EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
  echo ""
  echo "All tests passed!"
else
  echo ""
  echo "Some tests failed (exit code $EXIT_CODE)."
fi
exit $EXIT_CODE
