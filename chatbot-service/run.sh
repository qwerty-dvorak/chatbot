#!/bin/bash
# Unified entrypoint for chatbot-service.
# Sources model endpoints from models/.env.local or models/.env.runpod,
# then delegates to the shared start script.
#
# Usage:
#   bash run.sh local          # local GPU mode
#   bash run.sh runpod         # RunPod mode
#
# Infrastructure prerequisites:
#   - PostgreSQL: db/start.sh
#   - Milvus:     milvus/start.sh
#   - Models:     deploy-local-gemma.sh + deploy-local-others.sh (local) or deploy-runpod.sh (runpod)
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
    exec bash "$SCRIPT_DIR/shell_scripts/start-services.sh" "$@"
    ;;
  *)
    echo "Usage: bash run.sh {local|runpod|no-gemma|debug}"
    echo ""
    echo "  local     Start with local GPU (requires deploy-local-gemma.sh + deploy-local-others.sh)"
    echo "  runpod    Start with RunPod cloud GPU (requires deploy-runpod.sh)"
    exit 1
    ;;
esac
