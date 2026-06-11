#!/bin/bash
# Start the chatbot stack with verbose debug logging enabled.
#
# Sets CHAT_DEBUG=1 (logs all LLM API calls with truncated payloads),
# APPS_LOG_LEVEL=DEBUG, and DJANGO_REQUEST_LOG_LEVEL=DEBUG so every
# HTTP request is logged with method, path, status, and duration.
#
# Usage:
#   bash start-debug.sh                          # start + follow logs
#   bash start-debug.sh --no-tail                # start without log tail
#
# After starting, open http://localhost:8080
# Press Ctrl+C to stop everything.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

NO_TAIL=false
[[ "${1:-}" == "--no-tail" ]] && NO_TAIL=true

export CHAT_DEBUG=1
export APPS_LOG_LEVEL=DEBUG
export DJANGO_LOG_LEVEL=INFO
export DJANGO_REQUEST_LOG_LEVEL=DEBUG
export LOG_LEVEL=DEBUG

echo "══════════════════════════════════════════════════"
echo " Starting chatbot in DEBUG mode"
echo "  CHAT_DEBUG=$CHAT_DEBUG"
echo "  APPS_LOG_LEVEL=$APPS_LOG_LEVEL"
echo "  DJANGO_REQUEST_LOG_LEVEL=$DJANGO_REQUEST_LOG_LEVEL"
echo "══════════════════════════════════════════════════"
echo ""

# Start the mock stack (no GPU needed for debugging)
bash "$SCRIPT_DIR/start-services-no-gemma.sh"

echo ""
echo "══════════════════════════════════════════════════"
echo " Streaming logs..."
echo " Press Ctrl+C to stop"
echo "══════════════════════════════════════════════════"

if ! $NO_TAIL; then
  docker logs -f web worker 2>&1 | head -200 || true
fi
