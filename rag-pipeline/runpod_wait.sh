#!/bin/bash
# Wait for 3 RunPod vLLM pods to be RUNNING and model downloads to complete.
# Milvus runs locally via runpod_start_local_milvus.sh (no Milvus pod).
# Writes .env.runpod pointing at RunPod proxy URLs + localhost:19530.
#
# Usage: bash runpod_wait.sh
# Requires: .runpod_state (written by runpod_deploy.sh)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_state"
ENV_FILE="$SCRIPT_DIR/.env.runpod"

[[ -f "$STATE_FILE" ]] || { echo "ERROR: run runpod_deploy.sh first"; exit 1; }
source "$STATE_FILE"

# ── Helpers ───────────────────────────────────────────────────────────────────
pod_status() {
  runpodctl pod get "$1" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('desiredStatus') or d.get('status','UNKNOWN'))" \
    2>/dev/null || echo "UNKNOWN"
}

pod_public_ip() {
  runpodctl pod get "$1" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
# Try runtime.ports list
for p in ((d.get('runtime') or {}).get('ports') or []):
    ip = p.get('ip') or p.get('publicIp') or ''
    if ip and ip not in ('', '0.0.0.0'):
        print(ip); sys.exit(0)
# Fallback to top-level field
print(d.get('publicIp') or d.get('public_ip') or '')
" 2>/dev/null || echo ""
}

wait_running() {
  local name="$1" pod_id="$2" max="${3:-300}"
  local elapsed=0
  echo -n "  $name ($pod_id) RUNNING"
  while true; do
    local s; s=$(pod_status "$pod_id")
    [[ "$s" == "RUNNING" ]] && { echo " ✓"; return 0; }
    if [[ "$s" == "EXITED" || "$s" == "FAILED" ]]; then
      echo " ✗ (status=$s)"; echo "  Check: runpodctl pod get $pod_id"; return 1
    fi
    sleep 15; elapsed=$((elapsed + 15)); echo -n "."
    [[ $elapsed -ge $max ]] && { echo " TIMEOUT"; return 1; }
  done
}

wait_http() {
  local name="$1" url="$2" max="${3:-1800}"
  local elapsed=0
  echo -n "  $name healthy"
  while true; do
    local code; code=$(curl -sk -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    [[ "$code" == "200" ]] && { echo " ✓"; return 0; }
    sleep 30; elapsed=$((elapsed + 30)); echo -n "."
    [[ $elapsed -ge $max ]] && {
      echo " TIMEOUT (last HTTP $code)"
      echo "  Model download may still be in progress — re-run runpod_wait.sh"
      return 1
    }
  done
}

wait_tcp() {
  local name="$1" host="$2" port="$3" max="${4:-300}"
  local elapsed=0
  echo -n "  $name TCP $host:$port"
  while true; do
    timeout 3 bash -c ">/dev/tcp/$host/$port" 2>/dev/null && { echo " ✓"; return 0; }
    sleep 10; elapsed=$((elapsed + 10)); echo -n "."
    [[ $elapsed -ge $max ]] && { echo " TIMEOUT"; return 1; }
  done
}

# ─────────────────────────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo " Phase 1: waiting for 3 vLLM pods to reach RUNNING"
echo "══════════════════════════════════════════════════"
wait_running "text-embed" "$TEXT_POD"
wait_running "mm-embed"   "$MM_POD"
wait_running "reranker"   "$RERANKER_POD"

echo ""
echo "══════════════════════════════════════════════════"
echo " Phase 2: connection info"
echo "══════════════════════════════════════════════════"

TEXT_URL="https://${TEXT_POD}-8000.proxy.runpod.net"
MM_URL="https://${MM_POD}-8000.proxy.runpod.net"
RERANKER_URL="https://${RERANKER_POD}-8000.proxy.runpod.net"
MILVUS_IP="localhost"   # Milvus runs locally via runpod_start_local_milvus.sh

echo "  text-embed  -> $TEXT_URL"
echo "  mm-embed    -> $MM_URL"
echo "  reranker    -> $RERANKER_URL"
echo "  milvus      -> localhost:19530  (start with: bash runpod_start_local_milvus.sh)"

echo ""
echo "══════════════════════════════════════════════════"
echo " Phase 3: waiting for vLLM servers (model download"
echo "          ~16 GB each on RTX 3090, ~15-30 min)"
echo "══════════════════════════════════════════════════"
wait_http "text-embed" "$TEXT_URL/health"
wait_http "mm-embed"   "$MM_URL/health"
wait_http "reranker"   "$RERANKER_URL/health"

# ── Detect actual embedding dims from live API ────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Phase 4: detecting embedding dimensions"
echo "══════════════════════════════════════════════════"

detect_dim() {
  local url="$1" model="$2" endpoint="${3:-/v1/embeddings}"
  python3 - <<PYEOF 2>/dev/null || echo ""
import urllib.request, json, sys
payload = json.dumps({"model": "$model", "input": "test"}).encode()
req = urllib.request.Request("$url$endpoint",
      data=payload, headers={"Content-Type": "application/json"}, method="POST")
try:
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    item = resp["data"][0]
    emb = item.get("embedding") or item["data"][0]
    print(len(emb))
except Exception as e:
    sys.exit(1)
PYEOF
}

TEXT_DIM=$(detect_dim "$TEXT_URL" "nvidia/llama-embed-nemotron-8b")
[[ -n "$TEXT_DIM" ]] && echo "  text-embed dim: $TEXT_DIM" || { TEXT_DIM=4096; echo "  text-embed dim: 4096 (fallback — verify manually)"; }

MM_DIM=$(detect_dim "$MM_URL" "nvidia/nemotron-colembed-vl-8b-v2" "/pooling")
[[ -n "$MM_DIM" ]] && echo "  mm-embed dim:   $MM_DIM" || { MM_DIM=4096; echo "  mm-embed dim: 4096 (fallback — verify manually)"; }

# ── Write .env.runpod ─────────────────────────────────────────────────────────
cat > "$ENV_FILE" <<EOF
# Auto-generated by runpod_wait.sh
# RunPod pods: text=$TEXT_POD mm=$MM_POD reranker=$RERANKER_POD
# Milvus: run locally with 'bash runpod_start_local_milvus.sh'

# Chat (not needed for basic ingest+search; query enhancements disabled below)
CHAT_BASE_URL=http://localhost:9000/v1
CHAT_API_KEY=mock
CHAT_MODEL=openai/mock-chat

# Text embedding — nvidia/llama-embed-nemotron-8b
EMBEDDING_BASE_URL=${TEXT_URL}/v1
EMBEDDING_API_KEY=dummy
TEXT_EMBEDDING_MODEL=nvidia/llama-embed-nemotron-8b
TEXT_EMBEDDING_DIM=${TEXT_DIM}

# Multimodal embedding — nvidia/nemotron-colembed-vl-8b-v2
MULTIMODAL_EMBEDDING_BASE_URL=${MM_URL}
MULTIMODAL_EMBEDDING_API_KEY=dummy
MULTIMODAL_EMBEDDING_MODEL=nvidia/nemotron-colembed-vl-8b-v2
MULTIMODAL_EMBEDDING_DIM=${MM_DIM}

# Reranker — Qwen/Qwen3-VL-Reranker-8B (vLLM /score endpoint)
RERANKER_BASE_URL=${RERANKER_URL}
RERANKER_API_KEY=dummy
RERANKER_MODEL=Qwen/Qwen3-VL-Reranker-8B

# Milvus
MILVUS_HOST=${MILVUS_IP}
MILVUS_PORT=19530

# Collections
TEXT_COLLECTION=rag_text_chunks
IMAGE_COLLECTION=rag_image_chunks

# BM25
BM25_INDEX_PATH=/app/data/bm25_index.pkl

# Chunking
CHUNK_STRATEGY=recursive
CHUNK_SIZE=512
CHUNK_OVERLAP=64
SENTENCE_WINDOW_SIZE=3
PARENT_CHUNK_SIZE=2048

# Retrieval
RETRIEVAL_TOP_K=20
RERANK_TOP_K=5
HYBRID_ALPHA=0.5

# Query enhancements disabled (no chat model in this 4-pod config)
# To enable, add a 5th pod with a chat model and update CHAT_BASE_URL + CHAT_MODEL
QUERY_ENHANCEMENTS=

HYPOTHETICAL_QUESTIONS_PER_CHUNK=3
EOF

# append URLs to state file for other scripts
cat >> "$STATE_FILE" <<EOF
MILVUS_IP=${MILVUS_IP}
TEXT_URL=${TEXT_URL}
MM_URL=${MM_URL}
RERANKER_URL=${RERANKER_URL}
TEXT_DIM=${TEXT_DIM}
MM_DIM=${MM_DIM}
EOF

echo ""
echo "══════════════════════════════════════════════════"
echo " All services ready!"
echo "══════════════════════════════════════════════════"
echo ""
echo "  Milvus gRPC   $MILVUS_IP:19530"
echo "  Text Embed    $TEXT_URL"
echo "  MM Embed      $MM_URL"
echo "  Reranker      $RERANKER_URL"
echo ""
echo "  .env.runpod written. Run: bash test_api.sh"
