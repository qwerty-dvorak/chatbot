#!/bin/bash
# Orchestrate the full chatbot test suite: start services, wait for health, run tests.
#
# Mode selection:
#   --runpod   Use RunPod cloud GPU for chat LLM
#   --local    Use local GPU
#
# Flags:
#   --clean    Tear down services after tests
#   --keepdb   Reuse test database between runs
#   --no-start Assume services are already running
#
# Usage:
#   bash tests/chatbot/test-all.sh                         # detect mode
#   bash tests/chatbot/test-all.sh --runpod                # RunPod mode
#   bash tests/chatbot/test-all.sh --local                 # local GPU mode
#   bash tests/chatbot/test-all.sh --runpod --clean        # test then teardown
#   bash tests/chatbot/test-all.sh --keepdb apps.chat.tests
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICE_DIR="$ROOT_DIR/chatbot-service"
STATE_FILE="$ROOT_DIR/models/.runpod_state"
NETWORK_NAME="chatbot_net"
TEST_IMAGE="chatbot-test"

POSTGRES_USER="${POSTGRES_USER:-chatbot}"
POSTGRES_DB="${POSTGRES_DB:-chatbot}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}"

MODE=""
CLEAN=false
NO_START=false
KEEP_DB=false
TEST_LABELS=()

for arg in "$@"; do
  case "$arg" in
    --runpod)  MODE="runpod"  ;;
    --local)   MODE="local"   ;;
    --clean)   CLEAN=true     ;;
    --no-start) NO_START=true ;;
    --keepdb)  KEEP_DB=true   ;;
    --help|-h)
      echo "Usage: bash tests/chatbot/test-all.sh [--runpod|--local] [--clean] [--keepdb] [--no-start] [test_labels...]"
      exit 0 ;;
    *) TEST_LABELS+=("$arg") ;;
  esac
done

if [[ -z "$MODE" ]]; then
  if [[ -f "$STATE_FILE" ]]; then
    MODE="runpod"
    echo "Detected RunPod state — using runpod mode."
  else
    echo "ERROR: Specify --runpod or --local"
    exit 1
  fi
fi

echo "╔══════════════════════════════════════════════════╗"
echo "║  test-all.sh — chatbot test orchestrator        ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Mode:       $MODE"
echo "║  Clean:      $CLEAN"
echo "║  Keep DB:    $KEEP_DB"
echo "║  No-start:   $NO_START"
echo "║  Labels:     ${TEST_LABELS[*]:-(all)}"
echo "╚══════════════════════════════════════════════════╝"

if [[ "$NO_START" == "false" ]]; then
  if [[ "$MODE" == "runpod" ]]; then
    source "$ROOT_DIR/models/.env.runpod"
    echo "═══ RunPod endpoints sourced (services assumed running) ═══"
  else
    source "$ROOT_DIR/models/.env.local"
    echo "═══ Starting services (mode: $MODE) ═══"
    bash "$SERVICE_DIR/shell_scripts/start-services.sh"
  fi
fi

echo ""
echo "═══ Ensuring CREATEDB privilege ═══"
docker exec chatbot-postgres su - postgres -c \
  "psql -c \"ALTER USER ${POSTGRES_USER} CREATEDB;\"" 2>/dev/null || true

echo ""
echo "═══ Building test image ═══"
docker build -f "$SCRIPT_DIR/Dockerfile" -t "$TEST_IMAGE" "$ROOT_DIR"

CHAT_BASE_URL="${CHAT_BASE_URL:-http://gemma-inference-server:8000/v1}"
CHAT_MODEL="${CHAT_MODEL:-openai/google/gemma-4-E4B-it}"
VISION_MODEL="${VISION_MODEL:-$CHAT_MODEL}"
RAG_ENABLED="${RAG_ENABLED:-false}"
TOOL_CALLS_ENABLED="${TOOL_CALLS_ENABLED:-true}"
CHAT_REASONING_ENABLED="${CHAT_REASONING_ENABLED:-true}"
CHAT_STREAMING_ENABLED="${CHAT_STREAMING_ENABLED:-true}"

ARGS=()
[[ "$KEEP_DB" == "true" ]] && ARGS+=("--keepdb")
ARGS+=("${TEST_LABELS[@]}")

echo ""
echo "═══ Running tests ═══"
echo "  URL:     $CHAT_BASE_URL"
echo "  Model:   $CHAT_MODEL"
echo "  Vision:  $VISION_MODEL"
echo "  RAG:     $RAG_ENABLED"
echo "  Tools:   $TOOL_CALLS_ENABLED"
echo "  Reason:  $CHAT_REASONING_ENABLED"
echo "  Stream:  $CHAT_STREAMING_ENABLED"

docker run --rm \
  --network "$NETWORK_NAME" \
  -e POSTGRES_HOST=chatbot-postgres \
  -e POSTGRES_PORT=5433 \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="${CHAT_API_KEY:-dummy}" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  -e VISION_MODEL="$VISION_MODEL" \
  -e RAG_ENABLED="$RAG_ENABLED" \
  -e TOOL_CALLS_ENABLED="$TOOL_CALLS_ENABLED" \
  -e CHAT_REASONING_ENABLED="$CHAT_REASONING_ENABLED" \
  -e CHAT_STREAMING_ENABLED="$CHAT_STREAMING_ENABLED" \
  "$TEST_IMAGE" \
  "${ARGS[@]}"

EXIT_CODE=$?

if [[ "$CLEAN" == "true" ]]; then
  echo ""
  echo "═══ Cleaning up ═══"
  docker rm -f web worker file-server 2>/dev/null || true
fi

exit $EXIT_CODE
