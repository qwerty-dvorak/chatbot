#!/bin/bash
# Check Milvus components: collection info, minio objects, etcd keys.
#
# Usage: bash check.sh
set -euo pipefail

echo "=== Milvus collections ==="
if docker exec milvus-standalone python3 -c "
from pymilvus import connections, utility
connections.connect(host='localhost', port='19530')
for c in utility.list_collections():
    info = utility.collection_info(c)
    print(f'  {c}: {info.row_count} rows')
" 2>/dev/null; then
  echo "  (connected)"
else
  echo "  (unable to connect — Milvus may not be running)"
fi

echo ""
echo "=== MinIO buckets ==="
if docker exec milvus-minio ls /minio_data 2>/dev/null; then
  echo ""
  echo "=== MinIO a-bucket contents ==="
  docker exec milvus-minio ls /minio_data/a-bucket/ 2>/dev/null || echo "  (no a-bucket)"
else
  echo "  (unable to list — MinIO may not be running)"
fi

echo ""
echo "=== Container status ==="
for c in milvus-standalone milvus-minio milvus-etcd; do
  status=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "not found")
  echo "  $c: $status"
done
