#!/bin/bash
# Start local Milvus standalone for the RAG pipeline.
#
# This script is a convenience wrapper around the canonical local Milvus starter.
# For production use, set DOCKER_VOLUME_DIRECTORY to persist data:
#   DOCKER_VOLUME_DIRECTORY=/data/milvus bash start_milvus.sh
#
# Usage: bash start_milvus.sh
#        bash start_milvus.sh --clean
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "--clean" ]]; then
  exec bash "$SCRIPT_DIR/runpod_start_local_milvus.sh" --clean
fi

# Forward DOCKER_VOLUME_DIRECTORY for persistent storage, default to /tmp for tests
export DOCKER_VOLUME_DIRECTORY="${DOCKER_VOLUME_DIRECTORY:-/tmp/milvus-data}"
exec bash "$SCRIPT_DIR/runpod_start_local_milvus.sh"
