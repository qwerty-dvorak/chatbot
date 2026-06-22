#!/bin/bash
# Stop all RunPod pods.
# Reads pod IDs from .env.runpod.
#
# Usage: bash teardown-runpod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.runpod"

if [[ ! -f "$ENV_FILE" ]]; then
  # Fallback: find pods by name via runpodctl
  echo "No .env.runpod found — querying runpodctl for named pods..."
  for name in chat-llm rag-text-embed rag-mm-embed rag-reranker paddleocr-vl; do
    id=$(runpodctl pod list -o json 2>/dev/null | python3 -c "
import sys,json
try:
    for p in json.load(sys.stdin):
        if p.get('name') == '$name':
            print(p['id'])
except: pass
" 2>/dev/null)
    [[ -n "$id" ]] && { echo "  stopping $name ($id)..."; runpodctl pod stop "$id" 2>/dev/null || true; }
  done
  echo "Teardown complete."
  exit 0
fi

source "$ENV_FILE"

echo "Stopping RunPod pods..."
for pod_id in "${CHAT_POD:-}" "${TEXT_POD:-}" "${MM_POD:-}" "${RERANKER_POD:-}" "${OCR_POD:-}"; do
  [[ -z "$pod_id" ]] && continue
  out=$(runpodctl pod stop "$pod_id" 2>&1) && echo "  stopped pod $pod_id" \
    || echo "  pod $pod_id: $out (may already be stopped)"
done
echo ""
echo "Teardown complete."
