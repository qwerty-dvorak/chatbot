#!/bin/bash
# Start all mock services for local development and testing.
# No GPUs, no model downloads — everything runs with stdlib-only mock servers.
#
# Starts:
#   1. Mock AI model server (chat, embed, reranker, OCR on ports 9000-9004)
#   2. Mock RAG API server (all API endpoints on port 8093)
#   3. Milvus standalone (etcd + minio + milvus)
#
# Usage: bash start_mock_all.sh [--clean]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "--clean" ]]; then
  echo "Tearing down all mock services..."
  bash "$SCRIPT_DIR/mock_server/start.sh" --clean
  bash "$SCRIPT_DIR/mock_rag/start.sh" --clean
  bash "$SCRIPT_DIR/runpod_start_local_milvus.sh" --clean
  echo "All mock services stopped."
  exit 0
fi

echo "══════════════════════════════════════════════════"
echo " Starting all mock services"
echo "══════════════════════════════════════════════════"

# 1. Mock AI model server (5-in-1: chat, embed, pooling, reranker, OCR)
echo ""
echo "[1/3] Mock AI model server (ports 9000-9004)..."
bash "$SCRIPT_DIR/mock_server/start.sh" --build

# 2. Mock RAG API server
echo ""
echo "[2/3] Mock RAG API server (port 8093)..."
bash "$SCRIPT_DIR/mock_rag/start.sh" --build

# 3. Local Milvus
echo ""
echo "[3/3] Local Milvus (port 19530)..."
bash "$SCRIPT_DIR/runpod_start_local_milvus.sh"

echo ""
echo "══════════════════════════════════════════════════"
echo " All mock services started!"
echo "══════════════════════════════════════════════════"
echo ""
echo "  Mock AI model server:"
echo "    Chat completions  -> http://localhost:9000/v1/chat/completions"
echo "    Text embeddings   -> http://localhost:9001/v1/embeddings"
echo "    Multimodal embed  -> http://localhost:9002/pooling"
echo "    Reranker          -> http://localhost:9003/score"
echo "    OCR               -> http://localhost:9004/v1/chat/completions"
echo ""
echo "  Mock RAG API:"
echo "    http://localhost:8093  (all RAG pipeline endpoints)"
echo ""
echo "  Milvus:"
echo "    localhost:19530"
echo ""
echo "Run tests:"
echo "  bash mock_server/test_integration.sh"
echo "  bash mock_rag/test.sh"
echo ""
echo "Stop with:"
echo "  bash start_mock_all.sh --clean"
