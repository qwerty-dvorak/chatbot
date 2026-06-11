#!/bin/bash
# Start the ENTIRE BARC pipeline with all ML models on RunPod.
# Infrastructure (Milvus, PostgreSQL) runs locally in Docker.
# All ML models run on RunPod cloud GPUs.
#
# Prerequisites:
#   export HF_TOKEN=hf_...
#
# Usage:
#   bash start-full-runpod.sh
#   bash start-full-runpod.sh --clean
#
# To stop:
#   bash teardown-full-runpod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLEAN=false
for arg in "$@"; do case "$arg" in --clean) CLEAN=true ;; esac; done

if ! command -v runpodctl &>/dev/null; then echo "ERROR: runpodctl not found."; exit 1; fi
if [[ -z "${HF_TOKEN:-}" ]]; then echo "ERROR: HF_TOKEN required."; exit 1; fi

echo "╔══════════════════════════════════════════════════╗"
echo "║   BARC Pipeline — Full RunPod Launch             ║"
echo "╚══════════════════════════════════════════════════╝"

# Step 1: Deploy RunPod pods
echo ""
echo "═══ Step 1: Deploy RunPod pods ═══"
if [[ -f "$SCRIPT_DIR/models/.runpod_state" ]]; then
  echo "  RunPod pods already deployed. Skipping."
else
  echo "  Deploying all ML models on RunPod..."
  bash "$SCRIPT_DIR/models/deploy-runpod.sh"
fi

# Step 2: Start infrastructure
echo ""
echo "═══ Step 2: Start infrastructure (PostgreSQL + Milvus) ═══"
$CLEAN && bash "$SCRIPT_DIR/milvus/start.sh" --clean || bash "$SCRIPT_DIR/milvus/start.sh"
$CLEAN && bash "$SCRIPT_DIR/db/clear.sh" 2>/dev/null || true
bash "$SCRIPT_DIR/db/start.sh"

# Step 3: Start RAG pipeline
echo ""
echo "═══ Step 3: Start RAG pipeline ═══"
bash "$SCRIPT_DIR/rag-pipeline/run.sh runpod"

# Step 4: Start Chatbot service
echo ""
echo "═══ Step 4: Start Chatbot service ═══"
bash "$SCRIPT_DIR/chatbot-service/run.sh runpod"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   FULL PIPELINE IS RUNNING                        ║"
echo "╚══════════════════════════════════════════════════╝"
echo "  Web app:     http://localhost:8080"
echo "  RAG API:     http://localhost:8093/docs"
echo "  File server: http://localhost:8888/browse"
echo ""
echo " Tear down with: bash teardown-full-runpod.sh"
