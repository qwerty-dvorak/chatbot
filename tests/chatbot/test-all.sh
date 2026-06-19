#!/bin/bash
# Run Django tests against the live stack.
# Starts the full pipeline via root start.sh if not already running,
# then runs tests inside the web container via docker exec.
#
# Usage:
#   bash tests/chatbot/test-all.sh                         # detect mode, start stack, run tests
#   bash tests/chatbot/test-all.sh --keepdb                # keep test DB
#   bash tests/chatbot/test-all.sh --clean                 # teardown then restart
#   bash tests/chatbot/test-all.sh --no-start              # skip start.sh, web must be running
#   bash tests/chatbot/test-all.sh --keepdb apps.chat.tests
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

POSTGRES_CONTAINER="chatbot-postgres"
WEB_CONTAINER="web"

MODE=""
CLEAN=false
KEEP_DB=false
NO_START=false
TEST_LABELS=()

for arg in "$@"; do
  case "$arg" in
    --runpod)  MODE="runpod"  ;;
    --local)   MODE="local"   ;;
    --clean)   CLEAN=true     ;;
    --keepdb)  KEEP_DB=true   ;;
    --no-start) NO_START=true ;;
    --help|-h)
      echo "Usage: bash tests/chatbot/test-all.sh [--runpod|--local] [--clean] [--keepdb] [--no-start] [test_labels...]"
      exit 0 ;;
    *) TEST_LABELS+=("$arg") ;;
  esac
done

if [[ "$NO_START" == "false" ]]; then
  echo "═══ Starting pipeline ═══"
  START_ARGS=()
  [[ -n "$MODE" ]] && START_ARGS+=(--mode "$MODE")
  [[ "$CLEAN" == "true" ]] && START_ARGS+=(--clean)
  bash "$ROOT_DIR/start.sh" "${START_ARGS[@]}"
  echo ""
fi

if ! docker container inspect "$WEB_CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: '$WEB_CONTAINER' container is not running."
  echo "       Ensure the pipeline was started successfully."
  exit 1
fi

# Auto-detect mode from web container's CHAT_BASE_URL if not explicitly set
if [[ -z "$MODE" ]]; then
  CHAT_URL=$(docker exec "$WEB_CONTAINER" bash -c 'echo "${CHAT_BASE_URL:-}"')
  if echo "$CHAT_URL" | grep -qi "runpod"; then
    MODE="runpod"
  elif echo "$CHAT_URL" | grep -qiE "localhost|inference-server"; then
    MODE="local"
  else
    echo "ERROR: Could not auto-detect mode from CHAT_BASE_URL='$CHAT_URL'"
    echo "       Specify --runpod or --local explicitly."
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

echo ""
echo "═══ Ensuring CREATEDB privilege ═══"
docker exec "$POSTGRES_CONTAINER" su - postgres -c \
  "psql -c \"ALTER USER ${POSTGRES_USER:-chatbot} CREATEDB;\"" 2>/dev/null || true

ARGS=()
[[ "$KEEP_DB" == "true" ]] && ARGS+=("--keepdb")
ARGS+=("${TEST_LABELS[@]}")

echo ""
echo "═══ Running tests ═══"
docker exec "$WEB_CONTAINER" \
  env DJANGO_SETTINGS_MODULE=config.settings.test \
  uv run python manage.py test "${ARGS[@]}"

EXIT_CODE=$?

if [[ "$CLEAN" == "true" ]]; then
  echo ""
  echo "═══ Cleaning up ═══"
  docker rm -f web worker file-server 2>/dev/null || true
fi

exit $EXIT_CODE
