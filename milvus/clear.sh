#!/bin/bash
# WARNING: Destroys ALL Milvus, minio, and etcd data.
#
# Usage: bash clear.sh
set -euo pipefail

echo "WARNING: This will delete ALL Milvus data!"
read -rp "Type 'yes' to confirm: " confirm
[[ "$confirm" != "yes" ]] && { echo "Aborted."; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VOL_DIR="$SCRIPT_DIR/volumes"

for c in milvus-standalone milvus-minio milvus-etcd; do
  docker rm -f "$c" 2>/dev/null || true
done

rm -rf "$VOL_DIR/etcd" "$VOL_DIR/minio" "$VOL_DIR/milvus"
mkdir -p "$VOL_DIR/etcd" "$VOL_DIR/minio" "$VOL_DIR/milvus"

echo "Milvus data cleared. Run 'bash start.sh' to recreate."
