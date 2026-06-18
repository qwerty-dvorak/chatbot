#!/bin/bash
# Unified entrypoint for the entire BARC pipeline.
# Reads MODE from root .env and starts the full stack.
#
# Usage:
#   bash start.sh                          # start with mode from .env
#   bash start.sh --clean                  # teardown first then start
#   bash start.sh --mode local             # override mode to LOCAL
#   bash start.sh --mode runpod            # override mode to RUNPOD
#
# Prerequisites for RUNPOD mode:
#   export HF_TOKEN=hf_...
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CLEAN=false
MODE=""

for arg in "$@"; do
  case "$arg" in
    --clean)    CLEAN=true ;;
    --mode=*)   MODE="${arg#*=}" ;;
    --help|-h)
      echo "Usage: bash start.sh [--clean] [--mode local|runpod]"
      echo ""
      echo "  --clean        Tear down and restart"
      echo "  --mode local   Start with local GPU (default if no .env)"
      echo "  --mode runpod  Start with RunPod cloud GPU"
      exit 0 ;;
  esac
done

# Read MODE from root .env if not already set
if [[ -z "$MODE" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    MODE=$(grep -E '^MODE=' "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]' || echo "")
  fi
fi

# Normalise to uppercase
MODE="${MODE:-LOCAL}"
MODE="${MODE^^}"

if [[ "$MODE" != "LOCAL" && "$MODE" != "RUNPOD" ]]; then
  echo "ERROR: MODE must be LOCAL or RUNPOD (got: $MODE)"
  echo "Set MODE in $ENV_FILE or pass --mode local|runpod"
  exit 1
fi

echo "╔══════════════════════════════════════════════════╗"
echo "║   BARC Pipeline — Unified Launch                 ║"
echo "║   Mode: $MODE"
echo "╚══════════════════════════════════════════════════╝"

if [[ "$CLEAN" == "true" ]]; then
  bash "$SCRIPT_DIR/teardown.sh"
fi

# Step 1: Start infrastructure (Milvus + PostgreSQL)
echo ""
echo "═══ Step 1: Infrastructure ═══"
bash "$SCRIPT_DIR/milvus/start.sh"
$CLEAN && bash "$SCRIPT_DIR/db/clear.sh" 2>/dev/null || true
bash "$SCRIPT_DIR/db/start.sh"

# Step 2: Deploy models
echo ""
echo "═══ Step 2: Deploy models ($MODE) ═══"
if [[ "$MODE" == "RUNPOD" ]]; then
  if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "ERROR: HF_TOKEN is required for RUNPOD mode."
    echo "  export HF_TOKEN=hf_..."
    exit 1
  fi
  if [[ -f "$SCRIPT_DIR/models/.runpod_state" ]]; then
    echo "  RunPod pods already deployed. Skipping."
  else
    bash "$SCRIPT_DIR/models/deploy-runpod.sh"
  fi
else
  bash "$SCRIPT_DIR/models/deploy-local.sh"
fi

# Step 3: Start RAG pipeline
echo ""
echo "═══ Step 3: RAG pipeline ($MODE) ═══"
bash "$SCRIPT_DIR/rag-pipeline/run.sh" "${MODE,,}"

# Step 4: Start Chatbot service
echo ""
echo "═══ Step 4: Chatbot service ($MODE) ═══"
bash "$SCRIPT_DIR/chatbot-service/run.sh" "${MODE,,}"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   FULL PIPELINE IS RUNNING ($MODE)               ║"
echo "╚══════════════════════════════════════════════════╝"
echo "  Web app:     http://localhost:8080"
echo "  RAG API:     http://localhost:8093/docs"
echo "  File server: http://localhost:8888/browse"
echo ""
echo " Tear down with: bash teardown.sh"
