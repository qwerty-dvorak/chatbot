#!/usr/bin/env bash
# Hybrid Retrieve & Reranking Workflow — end-to-end integration test.
#
# Validates the dual-channel architecture:
#   ┌─ Vector Store ──→ top_k pool ─┐
#   │                               ├──→ Reranker (Cross-Encoder) → LLM
#   └─ BM25 Retrieval ─→ top_k pool ─┘
#
# Usage:
#   export POSTGRES_HOST=localhost MILVUS_HOST=localhost
#   bash test_hybrid_rerank.sh
#
# The .env file is loaded by pipeline.config automatically.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$HERE/../.." && pwd)"

# Override Docker hostnames for local access
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export MILVUS_HOST="${MILVUS_HOST:-localhost}"

echo "══════════════════════════════════════════════════"
echo " Hybrid Retrieve & Reranking Workflow Test"
echo "══════════════════════════════════════════════════"
echo ""

# Check prerequisites
echo "Checking prerequisites..."
docker exec rag-api uv run python -c "
from pymilvus import MilvusClient
import os
c = MilvusClient(f'http://{os.environ.get(\"MILVUS_HOST\",\"localhost\")}:{os.environ.get(\"MILVUS_PORT\",\"19530\")}')
c.list_collections()
print('  [PASS] Milvus reachable')
" 2>&1 || { echo "  [FAIL] Milvus unreachable"; exit 1; }
docker exec rag-api uv run python -c "
import os, psycopg2
conn = psycopg2.connect(host=os.environ.get('POSTGRES_HOST','localhost'), dbname=os.environ.get('POSTGRES_DB','chatbot'), user=os.environ.get('POSTGRES_USER','chatbot'), password=os.environ.get('POSTGRES_PASSWORD','chatbot'))
conn.close()
print('  [PASS] PostgreSQL reachable')
" 2>&1 || { echo "  [FAIL] PostgreSQL unreachable"; exit 1; }

echo ""
echo "Running hybrid+reranker test..."
docker run --rm --network rag_net \
  -v "$ROOT_DIR/rag-pipeline:/app" \
  rag-api uv run python /app/tests/test_hybrid_rerank.py
