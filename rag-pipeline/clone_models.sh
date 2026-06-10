#!/bin/bash
set -e

echo "Installing git-lfs if not present..."
which git-lfs >/dev/null 2>&1 || (apt-get install -y git-lfs 2>/dev/null || yum install -y git-lfs 2>/dev/null || brew install git-lfs 2>/dev/null || { echo "Please install git-lfs manually: https://git-lfs.github.com/"; exit 1; })
git lfs install

MODELS_DIR="$(cd "$(dirname "$0")/../.." && pwd)/models"

clone_or_pull() {
  local repo_url=$1
  local dest_dir=$2
  local name=$3

  if [ -d "$dest_dir/.git" ]; then
    echo "[$name] Already cloned, pulling latest..."
    git -C "$dest_dir" pull
  else
    echo "[$name] Cloning from $repo_url ..."
    git clone "$repo_url" "$dest_dir"
  fi
}

clone_or_pull "https://huggingface.co/nvidia/llama-embed-nemotron-8b" \
  "$MODELS_DIR/llama-embed-nemotron-8b" "llama-embed-nemotron-8b"

clone_or_pull "https://huggingface.co/nvidia/nemotron-colembed-vl-8b-v2" \
  "$MODELS_DIR/nemotron-colembed-vl-8b-v2" "nemotron-colembed-vl-8b-v2"

clone_or_pull "https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B" \
  "$MODELS_DIR/Qwen3-VL-Reranker-2B" "Qwen3-VL-Reranker-2B"

echo ""
echo "All models cloned to $MODELS_DIR"
echo "  Text embedding:        $MODELS_DIR/llama-embed-nemotron-8b"
echo "  Multimodal embedding:  $MODELS_DIR/nemotron-colembed-vl-8b-v2"
echo "  Reranker:              $MODELS_DIR/Qwen3-VL-Reranker-2B"
