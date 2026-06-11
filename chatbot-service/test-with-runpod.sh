#!/bin/bash
# Run the test suite against a live RunPod LLM endpoint.
#
# Reads .runpod_chat_state (written by runpod_deploy_chat.sh) to find the
# RunPod proxy URL, then runs tests via the Docker test runner.
#
# Usage:
#   bash test-with-runpod.sh                        # all tests
#   bash test-with-runpod.sh apps.chat.tests         # specific app
#   bash test-with-runpod.sh --keepdb                # keep test DB
#
# Prerequisites:
#   1. RunPod pod must be deployed and healthy:
#        bash runpod_deploy_chat.sh
#   2. Postgres must be running:
#        docker start postgres
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"

# ── Discover RunPod endpoint ────────────────────────────────────────────────
if [[ -f "$STATE_FILE" ]]; then
  source "$STATE_FILE"
  echo "RunPod chat state loaded: ${CHAT_URL:-<missing>}"
else
  echo "ERROR: $STATE_FILE not found."
  echo "Run 'bash runpod_deploy_chat.sh' first."
  exit 1
fi

CHAT_BASE_URL="${CHAT_URL}/v1"
CHAT_MODEL="${CHAT_MODEL:-openai/google/gemma-4-E4B-it}"

echo "Endpoint: $CHAT_BASE_URL"
echo "Model:    $CHAT_MODEL"
echo ""

# ── Verify postgres ─────────────────────────────────────────────────────────
if ! docker container inspect postgres >/dev/null 2>&1; then
  echo "ERROR: 'postgres' container is not running."
  echo "Start it: docker start postgres"
  exit 1
fi

# ── Ensure CREATEDB ─────────────────────────────────────────────────────────
docker exec postgres su - postgres -c \
  "psql -c \"ALTER USER ${POSTGRES_USER:-chatbot} CREATEDB;\"" 2>/dev/null || true

# ── Build test image if needed ──────────────────────────────────────────────
TEST_IMAGE="chatbot-test"
if ! docker image inspect "$TEST_IMAGE" >/dev/null 2>&1; then
  echo "Building test image..."
  docker build -f "$SCRIPT_DIR/Dockerfile.test" -t "$TEST_IMAGE" "$SCRIPT_DIR"
fi

# ── Run tests ────────────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo " Running tests against RunPod endpoint"
echo " URL:   $CHAT_BASE_URL"
echo " Model: $CHAT_MODEL"
echo "══════════════════════════════════════════════════"
echo ""

docker run --rm \
  --network "${NETWORK_NAME:-chatbot_net}" \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="${POSTGRES_DB:-chatbot}" \
  -e POSTGRES_USER="${POSTGRES_USER:-chatbot}" \
  -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}" \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-local-placeholder}" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  "$TEST_IMAGE" \
  "$@"

EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
  echo "All tests passed!"
else
  echo "Some tests failed (exit code $EXIT_CODE)."
fi
exit $EXIT_CODE
