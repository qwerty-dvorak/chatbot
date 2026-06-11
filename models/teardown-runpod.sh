#!/bin/bash
# Stop and delete all RunPod pods and templates.
# Reads pod/template IDs from .runpod_state.
#
# Usage: bash teardown-runpod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$SCRIPT_DIR/.runpod_state"

[[ -f "$STATE_FILE" ]] || { echo "No .runpod_state found — nothing to tear down."; exit 0; }
source "$STATE_FILE"

echo "Deleting RunPod pods..."
for pod_id in "${CHAT_POD:-}" "${TEXT_POD:-}" "${MM_POD:-}" "${RERANKER_POD:-}"; do
  [[ -z "$pod_id" ]] && continue
  out=$(runpodctl pod delete "$pod_id" 2>&1) && echo "  deleted pod $pod_id" \
    || echo "  pod $pod_id: $out (may already be gone)"
done

echo "Deleting RunPod templates..."
for tpl_id in "${CHAT_TPL:-}" "${TEXT_TPL:-}" "${MM_TPL:-}" "${RERANKER_TPL:-}"; do
  [[ -z "$tpl_id" ]] && continue
  out=$(runpodctl template delete "$tpl_id" 2>&1) && echo "  deleted template $tpl_id" \
    || echo "  template $tpl_id: $out (may already be gone)"
done

rm -f "$STATE_FILE"
echo ""
echo "Teardown complete."
