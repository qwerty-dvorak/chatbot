#!/bin/bash
# Shared LoRA discovery for deploy-local.sh and deploy-runpod.sh.
# Expected layout: <root>/<provider>/<repository>/adapter_config.json

lora_rank_ceiling() {
  local rank="$1"
  case "$rank" in
    ''|*[!0-9]*) return 1 ;;
  esac

  local allowed
  for allowed in 1 8 16 32 64 128 256 320 512; do
    if (( rank <= allowed )); then
      echo "$allowed"
      return 0
    fi
  done
  return 1
}

discover_lora_adapters() {
  local root="$1" expected_base_model="$2"
  LORA_NAMES=()
  LORA_DIRS=()
  LORA_GIT_URLS=()
  LORA_NAMES_CSV=""
  LORA_MAX_RANK=1

  [[ -d "$root" ]] || return 0

  local config adapter_dir provider repo name adapter_base rank rank_ceiling git_url
  while IFS= read -r -d '' config; do
    adapter_dir=$(dirname "$config")
    repo=$(basename "$adapter_dir")
    provider=$(basename "$(dirname "$adapter_dir")")

    if [[ ! -f "$adapter_dir/adapter_model.safetensors" ]]; then
      echo "WARNING: skipping $provider/$repo: adapter_model.safetensors is missing" >&2
      continue
    fi

    read -r adapter_base rank < <(python3 - "$config" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    config = json.load(fh)
print(config.get("base_model_name_or_path", ""), config.get("r", ""))
PY
)

    if [[ -n "$expected_base_model" && "${adapter_base##*/}" != "${expected_base_model##*/}" ]]; then
      echo "WARNING: skipping $provider/$repo: base model '$adapter_base' does not match '$expected_base_model'" >&2
      continue
    fi

    if ! rank_ceiling=$(lora_rank_ceiling "$rank"); then
      echo "WARNING: skipping $provider/$repo: unsupported LoRA rank '$rank'" >&2
      continue
    fi

    name="${repo%-gemma-4-*}"
    [[ "$name" == "$repo" ]] && name="$repo"
    if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
      echo "WARNING: skipping $provider/$repo: invalid vLLM module name '$name'" >&2
      continue
    fi
    if [[ ",${LORA_NAMES_CSV}," == *",${name},"* ]]; then
      echo "ERROR: duplicate LoRA module name '$name' from $provider/$repo" >&2
      return 1
    fi

    git_url=$(git -C "$adapter_dir" remote get-url origin 2>/dev/null || true)
    [[ -n "$git_url" ]] || git_url="https://huggingface.co/${provider}/${repo}"

    LORA_NAMES+=("$name")
    LORA_DIRS+=("$adapter_dir")
    LORA_GIT_URLS+=("$git_url")
    LORA_NAMES_CSV="${LORA_NAMES_CSV:+${LORA_NAMES_CSV},}${name}"
    (( rank_ceiling > LORA_MAX_RANK )) && LORA_MAX_RANK="$rank_ceiling"
  done < <(find -L "$root" -mindepth 3 -maxdepth 3 -type f -name adapter_config.json -print0 | sort -z)
}

# persist_lora_names removed — LoRA adapters are discovered at runtime
# by the chatbot-service via GET /v1/models on the chat endpoint.
