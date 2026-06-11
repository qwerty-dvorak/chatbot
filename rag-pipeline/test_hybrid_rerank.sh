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

# Override Docker hostnames for local access
export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export MILVUS_HOST="${MILVUS_HOST:-localhost}"

echo "══════════════════════════════════════════════════"
echo " Hybrid Retrieve & Reranking Workflow Test"
echo "══════════════════════════════════════════════════"
echo ""

# Check prerequisites
echo "Checking prerequisites..."
python3 -c "from pymilvus import MilvusClient; c = MilvusClient(host='$MILVUS_HOST', port='${MILVUS_PORT:-19530}'); c.list_collections(); print('  [PASS] Milvus reachable')" 2>&1 || { echo "  [FAIL] Milvus unreachable"; exit 1; }
python3 -c "
import psycopg2
conn = psycopg2.connect(host='$POSTGRES_HOST', dbname='${POSTGRES_DB:-chatbot}', user='${POSTGRES_USER:-chatbot}', password='${POSTGRES_PASSWORD:-chatbot}')
conn.close()
print('  [PASS] PostgreSQL reachable')
" 2>&1 || { echo "  [FAIL] PostgreSQL unreachable"; exit 1; }

echo ""
echo "Running hybrid+reranker test..."
cd "$HERE"
python3 tests/test_hybrid_rerank.py
