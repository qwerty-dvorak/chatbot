#!/bin/bash
# Wait for the chat LLM RunPod pod to be RUNNING and vLLM model to be healthy.
# Writes CHAT_BASE_URL to .runpod_chat_state for start-services-with-runpod.sh.
#
# Usage: bash runpod_wait_chat.sh
# Requires: .runpod_chat_state (written by runpod_deploy_chat.sh)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"

[[ -f "$STATE_FILE" ]] || { echo "ERROR: run runpod_deploy_chat.sh first"; exit 1; }
source "$STATE_FILE"

# ── Helpers ───────────────────────────────────────────────────────────────────
pod_status() {
  runpodctl pod get "$1" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('desiredStatus') or d.get('status','UNKNOWN'))" \
    2>/dev/null || echo "UNKNOWN"
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
      echo "  Model download may still be in progress — re-run runpod_wait_chat.sh"
      return 1
    }
  done
}

# ─────────────────────────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo " Phase 1: waiting for chat-llm pod to reach RUNNING"
echo "══════════════════════════════════════════════════"
wait_running "chat-llm" "$CHAT_POD"

echo ""
echo "══════════════════════════════════════════════════"
echo " Phase 2: connection info"
echo "══════════════════════════════════════════════════"

CHAT_URL="https://${CHAT_POD}-8000.proxy.runpod.net"
echo "  chat-llm  -> $CHAT_URL"
echo "  endpoint  -> $CHAT_URL/v1"
  echo "  model     -> google/gemma-4-E4B-it"

echo ""
echo "══════════════════════════════════════════════════"
echo " Phase 3: waiting for vLLM server (model download"
echo "          ~24 GB, may take 15-30 min)"
echo "══════════════════════════════════════════════════"
wait_http "chat-llm" "$CHAT_URL/health"

# ── Append URL to state file ──────────────────────────────────────────────────
cat >> "$STATE_FILE" <<EOF
CHAT_URL=${CHAT_URL}
EOF

echo ""
echo "══════════════════════════════════════════════════"
echo " Chat LLM pod is ready!"
echo "══════════════════════════════════════════════════"
echo ""
echo "  URL:      $CHAT_URL"
echo "  API base: $CHAT_URL/v1"
  echo "  Model:    google/gemma-4-E4B-it"
echo ""
echo "  Now start the local stack:"
echo "    bash start-services-with-runpod.sh"
