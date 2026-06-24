#!/bin/bash
# Unified entrypoint for chatbot-service.
# Sources model endpoints from models/.env.local or models/.env.runpod,
# then delegates to the appropriate shell_scripts/ sub-command.
#
# Usage:
#   bash run.sh local          # local GPU mode (via shell_scripts/start-services.sh)
#   bash run.sh runpod         # RunPod mode     (via shell_scripts/start-services-with-runpod.sh)
#   bash run.sh no-gemma       # mock mode       (via shell_scripts/start-services-no-gemma.sh)
#   bash run.sh debug          # debug mode      (via shell_scripts/start-debug.sh)
#
# Infrastructure prerequisites:
#   - PostgreSQL: db/start.sh
#   - Milvus:     milvus/start.sh
#   - Models:     models/deploy-local-gemma.sh + models/deploy-local-others.sh (for local mode)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

case "${1:-}" in
  local)
    shift
    export $(grep -v '^#' "$ROOT_DIR/models/.env.local" 2>/dev/null | xargs)
    exec bash "$SCRIPT_DIR/shell_scripts/start-services.sh" "$@"
    ;;
  runpod)
    shift
    export $(grep -v '^#' "$ROOT_DIR/models/.env.runpod" 2>/dev/null | xargs)
    exec bash "$SCRIPT_DIR/shell_scripts/start-services-with-runpod.sh" "$@"
    ;;
  no-gemma|mock)
    shift
    exec bash "$SCRIPT_DIR/shell_scripts/start-services-no-gemma.sh" "$@"
    ;;
  debug)
    shift
    exec bash "$SCRIPT_DIR/shell_scripts/start-debug.sh" "$@"
    ;;
  *)
    echo "Usage: bash run.sh {local|runpod|no-gemma|debug}"
    echo ""
    echo "  local     Start with local GPU (requires models/deploy-local-gemma.sh + deploy-local-others.sh)"
    echo "  runpod    Start with RunPod cloud GPU (requires models/deploy-runpod.sh)"
    echo "  no-gemma  Start with mock models (no GPU required)"
    echo "  debug     Debug mode with verbose logging"
    exit 1
    ;;
esac
