#!/bin/bash
# Unified test orchestrator that reads MODE from root .env.
# Delegates to tests/run.sh with the appropriate flags.
#
# Usage:
#   bash test.sh                              # detect mode and run all tests
#   bash test.sh chatbot                      # run only chatbot tests
#   bash test.sh rag                          # run only rag-pipeline tests
#   bash test.sh integration                  # run integration tests
#   bash test.sh all                          # run all test suites
#   bash test.sh --mode local chatbot         # force local mode
#   bash test.sh --mode runpod chatbot        # force runpod mode
#   bash test.sh --clean chatbot              # teardown after tests
#   bash test.sh --no-start chatbot           # skip stack startup
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
MODE=""
CLEAN=false
NO_START=false
KEEP_DB=false
TEST_SUITE=""
EXTRA_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#*=}" ;;
    --clean)  CLEAN=true ;;
    --no-start) NO_START=true ;;
    --keepdb) KEEP_DB=true ;;
    chatbot|rag|integration|all) TEST_SUITE="$arg" ;;
    --help|-h)
      echo "Usage: bash test.sh [--mode local|runpod] [--clean] [--no-start] [--keepdb] {chatbot|rag|integration|all}"
      echo ""
      echo "  chatbot     Run chatbot-service Django tests"
      echo "  rag         Run rag-pipeline API tests"
      echo "  integration Run integration tests"
      echo "  all         Run all test suites"
      echo ""
      echo "  --mode      Override mode (default: from .env)"
      echo "  --clean     Tear down services after tests"
      echo "  --no-start  Skip stack startup (assume already running)"
      echo "  --keepdb    Reuse test database"
      exit 0 ;;
    *) EXTRA_ARGS+=("$arg") ;;
  esac
done

# Read MODE from root .env if not already set
if [[ -z "$MODE" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    MODE=$(grep -E '^MODE=' "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]' || echo "")
  fi
fi

MODE="${MODE:-LOCAL}"
MODE="${MODE^^}"

if [[ -z "$TEST_SUITE" ]]; then
  echo "Usage: bash test.sh [--mode local|runpod] {chatbot|rag|integration|all}"
  echo "  Use 'all' to run all test suites."
  exit 1
fi

echo "╔══════════════════════════════════════════════════╗"
echo "║   BARC Pipeline — Unified Test Runner            ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Mode:  $MODE"
echo "║  Suite: $TEST_SUITE"
echo "║  Clean: $CLEAN"
echo "╚══════════════════════════════════════════════════╝"

ARGS=()
if [[ "$MODE" == "RUNPOD" ]]; then
  ARGS+=("--runpod")
else
  ARGS+=("--local")
fi
$CLEAN    && ARGS+=("--clean")
$NO_START && ARGS+=("--no-start")
$KEEP_DB  && ARGS+=("--keepdb")
ARGS+=("${EXTRA_ARGS[@]}")

exec bash "$SCRIPT_DIR/tests/run.sh" "$TEST_SUITE" "${ARGS[@]}"
