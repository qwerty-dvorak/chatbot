#!/bin/bash
# Unified teardown for the entire BARC pipeline.
# Reads MODE from root .env and tears down the full stack.
#
# Usage:
#   bash teardown.sh                        # teardown with mode from .env
#   bash teardown.sh --mode runpod          # override mode to RUNPOD
#   bash teardown.sh --all                  # also remove volumes
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
MODE=""
REMOVE_VOLUMES=false

for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#*=}" ;;
    --all|-a)  REMOVE_VOLUMES=true ;;
    --help|-h)
      echo "Usage: bash teardown.sh [--mode local|runpod] [--all]"
      echo "  --mode local|runpod  Override mode (default: from .env)"
      echo "  --all                Also remove Docker volumes"
      exit 0 ;;
  esac
done

if [[ -z "$MODE" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    MODE=$(grep -E '^MODE=' "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]' || echo "")
  fi
fi

MODE="${MODE:-LOCAL}"
MODE="${MODE^^}"

echo "╔══════════════════════════════════════════════════╗"
echo "║   BARC Pipeline — Unified Teardown               ║"
echo "║   Mode: $MODE"
echo "╚══════════════════════════════════════════════════╝"

# Step 1: Stop chatbot services
echo ""
echo "═══ Step 1: Stop chatbot services ═══"
docker rm -f web worker file-server 2>/dev/null || true

# Step 2: Stop RAG pipeline
echo ""
echo "═══ Step 2: Stop RAG pipeline ═══"
docker rm -f rag-api 2>/dev/null || true
docker rm -f rag-text-embed rag-multimodal-embed rag-reranker gemma-inference 2>/dev/null || true

# Step 3: Teardown models
echo ""
echo "═══ Step 3: Teardown models ($MODE) ═══"
if [[ "$MODE" == "RUNPOD" ]]; then
  bash "$SCRIPT_DIR/models/teardown-runpod.sh" 2>/dev/null || echo "  (no RunPod pods to teardown)"
fi

# Step 4: Stop infrastructure
echo ""
echo "═══ Step 4: Stop infrastructure ═══"
bash "$SCRIPT_DIR/milvus/stop.sh" 2>/dev/null || true
bash "$SCRIPT_DIR/db/stop.sh" 2>/dev/null || true

# Step 5: Remove volumes (optional)
if [[ "$REMOVE_VOLUMES" == "true" ]]; then
  echo ""
  echo "═══ Step 5: Remove Docker volumes ═══"
  docker volume rm docs_data media_data postgres_data 2>/dev/null || true
fi

echo ""
echo "Teardown complete."
