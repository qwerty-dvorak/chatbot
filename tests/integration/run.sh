#!/bin/bash
# Integration test orchestrator.
# Starts chatbot + rag-pipeline, runs cross-service tests, tears down.
#
# Usage: bash tests/integration/run.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Integration tests — not yet implemented."
echo ""
echo "This will test:"
echo "  - Chat with RAG context injection"
echo "  - Document upload → search → chat flow"
echo "  - Cross-service API calls"
echo ""
echo "Expected flow:"
echo "  1. Start Milvus: bash milvus/start.sh"
echo "  2. Start DB:     bash db/start.sh"
echo "  3. Start RAG API:  bash rag-pipeline/run.sh runpod"
echo "  4. Start Chatbot:  bash chatbot-service/run.sh runpod"
echo "  5. Run integration tests"
echo "  6. Tear down"
exit 0
