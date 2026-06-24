#!/bin/bash
# Unified entrypoint for rag-pipeline.
# Sources model endpoints from models/.env.local or models/.env.runpod,
# then delegates to the appropriate shell_scripts/ sub-command.
#
# Usage:
#   bash run.sh local          # local GPU mode (via shell_scripts/start-services.sh)
#   bash run.sh runpod         # RunPod mode     (via shell_scripts/start-services-runpod.sh)
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
    export $(grep -v '^#' "$ROOT_DIR/models/.env.local" 2>/dev/null | xargs)
    exec bash "$SCRIPT_DIR/shell_scripts/start-services.sh"
    ;;
  runpod)
    export $(grep -v '^#' "$ROOT_DIR/models/.env.runpod" 2>/dev/null | xargs)
    exec bash "$SCRIPT_DIR/shell_scripts/start-services-runpod.sh"
    ;;
  *)
    echo "Usage: bash run.sh {local|runpod}"
    echo ""
    echo "  local     Start with local GPU (requires models/deploy-local-gemma.sh + deploy-local-others.sh)"
    echo "  runpod    Start with RunPod cloud GPU (requires models/deploy-runpod.sh)"
    exit 1
    ;;
esac
