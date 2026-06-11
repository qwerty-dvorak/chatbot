#!/bin/bash
# Stop Milvus, minio, and etcd containers.
#
# Usage: bash stop.sh
set -euo pipefail

for c in milvus-standalone milvus-minio milvus-etcd; do
  docker stop "$c" 2>/dev/null && echo "Stopped $c" || echo "$c not running"
done
