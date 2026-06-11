#!/bin/bash
# Orchestrate the full test suite: start services, wait for health, run tests.
#
# Mode selection:
#   --runpod   Use RunPod cloud GPU for chat LLM (default if .runpod_chat_state exists)
#   --local    Use local GPU via start-services.sh (requires 2x NVIDIA GPUs)
#
# Other flags:
#   --clean    Tear down services after tests run (docker rm + optional pod teardown)
#   --keepdb   Reuse test database between runs (faster)
#   --no-start Assume services are already running; skip startup
#
# Usage:
#   bash test-all.sh                          # detect mode from state file
#   bash test-all.sh --runpod                 # RunPod mode
#   bash test-all.sh --runpod --clean         # RunPod + teardown after
#   bash test-all.sh --local                  # local GPU mode
#   bash test-all.sh --runpod --keepdb        # RunPod + keep test DB
#   bash test-all.sh --runpod --no-start      # RunPod, don't start stack
#
# Pass through test labels:
#   bash test-all.sh apps.chat.tests          # specific app
#   bash test-all.sh apps.chat.tests.test_chat_api  # single file
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"
NETWORK_NAME="chatbot_net"
TEST_IMAGE="chatbot-test"

POSTGRES_USER="${POSTGRES_USER:-chatbot}"
POSTGRES_DB="${POSTGRES_DB:-chatbot}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chatbot}"

MODE=""             # runpod | local
CLEAN=false
NO_START=false
KEEP_DB=false
TEST_LABELS=()

# ── Parse flags ────────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --runpod)  MODE="runpod"  ;;
    --local)   MODE="local"   ;;
    --clean)   CLEAN=true     ;;
    --no-start) NO_START=true ;;
    --keepdb)  KEEP_DB=true   ;;
    --help|-h)
      echo "Usage: bash test-all.sh [--runpod|--local] [--clean] [--keepdb] [--no-start] [test_labels...]"
      echo ""
      echo "Modes:"
      echo "  --runpod   Use RunPod cloud GPU (default if .runpod_chat_state exists)"
      echo "  --local    Use local GPU via start-services.sh"
      echo ""
      echo "Flags:"
      echo "  --clean    Tear down services after tests"
      echo "  --keepdb   Reuse test database between runs"
      echo "  --no-start Skip service startup (assume already running)"
      echo ""
      echo "Examples:"
      echo "  bash test-all.sh"
      echo "  bash test-all.sh --runpod --clean"
      echo "  bash test-all.sh --runpod --keepdb apps.chat.tests"
      echo "  bash test-all.sh --local --clean"
      exit 0
      ;;
    *)
      TEST_LABELS+=("$arg") ;;
  esac
done

# ── Detect mode ────────────────────────────────────────────────────────────
if [[ -z "$MODE" ]]; then
  if [[ -f "$STATE_FILE" ]]; then
    MODE="runpod"
    echo "Detected RunPod state file — using runpod mode."
  else
    echo "ERROR: No mode specified and no .runpod_chat_state found."
    echo "  Use --runpod or --local, or run 'bash runpod_deploy_chat.sh' first."
    exit 1
  fi
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  test-all.sh — chatbot test orchestrator        ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Mode:       $MODE"
echo "║  Clean:      $CLEAN"
echo "║  Keep DB:    $KEEP_DB"
echo "║  No-start:   $NO_START"
echo "║  Labels:     ${TEST_LABELS[*]:-(all tests)}"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Start services ─────────────────────────────────────────────────────────
if [[ "$NO_START" == "false" ]]; then
  echo "═══ Starting services (mode: $MODE) ═══"

  if [[ "$MODE" == "runpod" ]]; then
    if [[ ! -f "$STATE_FILE" ]]; then
      echo "ERROR: .runpod_chat_state not found. Run 'bash runpod_deploy_chat.sh' first."
      exit 1
    fi
    bash "$SCRIPT_DIR/start-services-with-runpod.sh"
  else
    bash "$SCRIPT_DIR/start-services.sh"
  fi

  # Wait for postgres to be healthy
  echo ""
  echo "═══ Waiting for postgres ═══"
  for i in $(seq 1 30); do
    if docker container inspect postgres >/dev/null 2>&1 && \
       docker exec postgres pg_isready -q 2>/dev/null; then
      echo "  postgres is healthy."
      break
    fi
    sleep 3
    if [[ $i -eq 30 ]]; then
      echo "ERROR: postgres not healthy after 90s"
      exit 1
    fi
  done

  if [[ "$MODE" == "runpod" ]]; then
    # In runpod mode, wait for RunPod endpoint to be healthy too
    source "$STATE_FILE"
    RUNPOD_URL="${CHAT_URL}"
    echo ""
    echo "═══ Waiting for RunPod endpoint ═══"
    echo "  URL: $RUNPOD_URL/v1/models"
    for i in $(seq 1 60); do
      if curl -sf "${RUNPOD_URL}/v1/models" >/dev/null 2>&1; then
        echo "  RunPod endpoint is healthy."
        break
      fi
      sleep 10
      if [[ $i -eq 60 ]]; then
        echo "ERROR: RunPod endpoint not healthy after 10 min"
        exit 1
      fi
    done
  fi
fi

# ── Grant CREATEDB ──────────────────────────────────────────────────────────
echo ""
echo "═══ Ensuring CREATEDB privilege ═══"
docker exec postgres su - postgres -c \
  "psql -c \"ALTER USER ${POSTGRES_USER} CREATEDB;\"" 2>/dev/null || true

# ── Build test image ───────────────────────────────────────────────────────
echo ""
echo "═══ Building test image ═══"
docker build -f "$SCRIPT_DIR/Dockerfile.test" -t "$TEST_IMAGE" "$SCRIPT_DIR"

# ── Set endpoint vars ──────────────────────────────────────────────────────
if [[ "$MODE" == "runpod" ]]; then
  source "$STATE_FILE"
  CHAT_BASE_URL="${CHAT_URL}/v1"
  CHAT_MODEL="${CHAT_MODEL:-openai/google/gemma-4-E4B-it}"
  CHAT_API_KEY="${CHAT_API_KEY:-local-placeholder}"
else
  CHAT_BASE_URL="http://gemma-inference-server:8000/v1"
  CHAT_MODEL="${CHAT_MODEL:-openai//gemma4-26B-A4b}"
  CHAT_API_KEY="${CHAT_API_KEY:-local-placeholder}"
fi

# ── Assemble test args ─────────────────────────────────────────────────────
ARGS=()
if [[ "$KEEP_DB" == "true" ]]; then
  ARGS+=("--keepdb")
fi
ARGS+=("${TEST_LABELS[@]}")

# ── Run tests ──────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  Running tests"
echo "║  URL:   $CHAT_BASE_URL"
echo "║  Model: $CHAT_MODEL"
echo "║  Args:  ${ARGS[*]:-(none)}"
echo "╚══════════════════════════════════════════════════╝"
echo ""

docker run --rm \
  --network "$NETWORK_NAME" \
  -e POSTGRES_HOST=postgres \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB="$POSTGRES_DB" \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e CHAT_BASE_URL="$CHAT_BASE_URL" \
  -e CHAT_API_KEY="$CHAT_API_KEY" \
  -e CHAT_MODEL="$CHAT_MODEL" \
  "$TEST_IMAGE" \
  "${ARGS[@]}"

EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
  echo "All tests passed!"
else
  echo "Some tests failed (exit code $EXIT_CODE)."
fi

# ── Cleanup ────────────────────────────────────────────────────────────────
if [[ "$CLEAN" == "true" ]]; then
  echo ""
  echo "═══ Cleaning up ═══"

  if [[ "$MODE" == "runpod" ]]; then
    if [[ -f "$STATE_FILE" ]]; then
      source "$STATE_FILE"
      if [[ -n "${POD_ID:-}" ]]; then
        echo "Stopping RunPod pod $POD_ID..."
        bash "$SCRIPT_DIR/runpod_teardown_chat.sh" || true
      fi
    fi
  fi

  echo "Removing Docker containers..."
  docker rm -f web worker file-server postgres 2>/dev/null || true
  if [[ "$MODE" == "local" ]]; then
    docker rm -f gemma-inference-server 2>/dev/null || true
  else
    docker rm -f chatbot-mock-server 2>/dev/null || true
  fi
  docker network rm "$NETWORK_NAME" 2>/dev/null || true
  echo "Cleanup complete."
fi

exit $EXIT_CODE
