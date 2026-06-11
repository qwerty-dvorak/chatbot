#!/bin/bash
# Start the ENTIRE BARC pipeline with all ML models on RunPod.
# - Milvus, PostgreSQL, rag-api run locally in Docker
# - Chat LLM (gemma-4-E4B-it), Text Embed (llama-embed-nemotron),
#   Multimodal Embed (nemotron-colembed), Reranker (Qwen3-VL-Reranker)
#   all run on RunPod cloud GPUs
#
# Prerequisites:
#   export HF_TOKEN=hf_...   # required for RunPod deployment
#
# Usage:
#   bash start-full-runpod.sh
#   bash start-full-runpod.sh --clean   # full teardown + restart
#
# To stop:
#   bash teardown-full-runpod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

CLEAN=false
for arg in "$@"; do
  case "$arg" in --clean) CLEAN=true ;; esac
done

echo "╔══════════════════════════════════════════════════════════╗"
echo "║   BARC Pipeline — Full RunPod Launch                    ║"
echo "╚══════════════════════════════════════════════════════════╝"

# ── Step 0: Preflight ────────────────────────────────────────────────────────
if ! command -v runpodctl &>/dev/null; then
  echo "ERROR: runpodctl not found. Install it first."
  exit 1
fi
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERROR: HF_TOKEN is required."
  echo "  export HF_TOKEN=hf_..."
  exit 1
fi

# ── Step 1: Deploy RunPod pods (if not already deployed) ──────────────────────
echo ""
echo "═══ Step 1: Deploy RunPod pods ═══"

if [[ -f "$SCRIPT_DIR/chatbot-service/.runpod_chat_state" ]]; then
  echo "  Chat LLM pod already deployed. Skipping."
else
  echo "  Deploying chat LLM (gemma-4-E4B-it)..."
  bash "$SCRIPT_DIR/chatbot-service/runpod_deploy_chat.sh" --no-wait
fi

if [[ -f "$SCRIPT_DIR/rag-pipeline/.runpod_env" ]]; then
  echo "  RAG pods already deployed. Skipping."
else
  echo "  Deploying RAG pods (embed + reranker)..."
  bash "$SCRIPT_DIR/rag-pipeline/runpod_deploy.sh" --no-wait
fi

# Wait for all pods to be ready
echo "  Waiting for RunPod pods to be ready..."
if [[ -f "$SCRIPT_DIR/chatbot-service/.runpod_chat_state" ]]; then
  bash "$SCRIPT_DIR/chatbot-service/runpod_wait_chat.sh" 2>/dev/null || true
fi

# ── Step 2: Start RAG pipeline ────────────────────────────────────────────────
echo ""
echo "═══ Step 2: Start RAG pipeline (Milvus + PostgreSQL + rag-api) ═══"
$CLEAN && bash "$SCRIPT_DIR/rag-pipeline/start-services.sh" --clean
$CLEAN || bash "$SCRIPT_DIR/rag-pipeline/start-services.sh"

echo "  Waiting for RAG API to be healthy..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8093/health >/dev/null 2>&1; then
    echo "  RAG API is healthy!"
    break
  fi
  sleep 2
done

# ── Step 3: Start Chatbot service ─────────────────────────────────────────────
echo ""
echo "═══ Step 3: Start Chatbot service (web + worker + file-server) ═══"
$CLEAN && bash "$SCRIPT_DIR/chatbot-service/start-services-with-runpod.sh" --clean
$CLEAN || bash "$SCRIPT_DIR/chatbot-service/start-services-with-runpod.sh"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   FULL PIPELINE IS RUNNING                               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Web app:     http://localhost:8080"
echo "  RAG API:     http://localhost:8093/docs"
echo "  File server: http://localhost:8888/browse"
echo "  Milvus:      localhost:19530"
echo "  Postgres:    localhost:5432"
echo ""
echo "  Chat LLM:   https://<chat-pod-id>-8000.proxy.runpod.net"
echo "  Embed:      https://<embed-pod-id>-8000.proxy.runpod.net"
echo "  Reranker:   https://<reranker-pod-id>-8000.proxy.runpod.net"
echo ""
echo " Tear down with: bash teardown-full-runpod.sh"
echo "═══════════════════════════════════════════════════════════"
