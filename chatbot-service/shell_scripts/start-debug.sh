#!/bin/bash
# Start the chatbot stack with verbose debug logging enabled.
#
# Usage:
#   bash shell_scripts/start-debug.sh
#   bash shell_scripts/start-debug.sh --no-tail
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NO_TAIL=false
[[ "${1:-}" == "--no-tail" ]] && NO_TAIL=true

export CHAT_DEBUG=1
export APPS_LOG_LEVEL=DEBUG
export DJANGO_LOG_LEVEL=INFO
export DJANGO_REQUEST_LOG_LEVEL=DEBUG
export LOG_LEVEL=DEBUG

echo "Starting chatbot in DEBUG mode..."

bash "$SCRIPT_DIR/start-services-no-gemma.sh"

echo "Streaming logs... Press Ctrl+C to stop."

if ! $NO_TAIL; then
  docker logs -f web worker 2>&1 | head -200 || true
fi
