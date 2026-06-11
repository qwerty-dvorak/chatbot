#!/bin/bash
# Stop and delete the chat-llm RunPod pod and its template.
# Reads pod/template IDs from .runpod_chat_state.
#
# Usage: bash runpod_teardown_chat.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_chat_state"

[[ -f "$STATE_FILE" ]] || { echo "No .runpod_chat_state found — nothing to tear down."; exit 0; }
source "$STATE_FILE"

echo "Deleting RunPod pod..."
for pod_id in "${CHAT_POD:-}"; do
  [[ -z "$pod_id" ]] && continue
  out=$(runpodctl pod delete "$pod_id" 2>&1) && echo "  deleted pod $pod_id" \
    || echo "  pod $pod_id: $out (may already be gone)"
done

echo "Deleting RunPod template..."
for tpl_id in "${CHAT_TPL:-}"; do
  [[ -z "$tpl_id" ]] && continue
  out=$(runpodctl template delete "$tpl_id" 2>&1) && echo "  deleted template $tpl_id" \
    || echo "  template $tpl_id: $out (may already be gone)"
done

rm -f "$STATE_FILE"
echo ""
echo "Teardown complete."
