#!/bin/bash
# Start Milvus standalone with etcd and minio.
# Exposes ports: Milvus :19530, MinIO :9000/:9001, etcd :2379.
# Data volumes are persisted under milvus/volumes/.
#
# Usage:
#   bash start.sh
#   bash start.sh --clean   # tear down and restart
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NETWORK_NAME="milvus_net"
VOL_DIR="$SCRIPT_DIR/volumes"

mkdir -p "$VOL_DIR/etcd" "$VOL_DIR/minio" "$VOL_DIR/milvus"

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME"

if [[ "$*" == *"--clean"* ]]; then
  echo "Removing old Milvus containers..."
  docker rm -f milvus-standalone milvus-minio milvus-etcd 2>/dev/null || true
fi

echo "Starting etcd..."
docker rm -f milvus-etcd 2>/dev/null || true
docker run -d \
  --name milvus-etcd \
  --network "$NETWORK_NAME" \
  --network-alias etcd \
  -e ETCD_AUTO_COMPACTION_MODE=revision \
  -e ETCD_AUTO_COMPACTION_RETENTION=1000 \
  -e ETCD_QUOTA_BACKEND_BYTES=4294967296 \
  -e ETCD_SNAPSHOT_COUNT=50000 \
  -v "$VOL_DIR/etcd:/etcd" \
  --health-cmd="etcdctl endpoint health --cluster" \
  --health-interval=30s --health-timeout=20s --health-retries=3 \
  quay.io/coreos/etcd:v3.7.0-rc.0 \
  etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

echo "Starting minio..."
docker rm -f milvus-minio 2>/dev/null || true
docker run -d \
  --name milvus-minio \
  --network "$NETWORK_NAME" \
  --network-alias minio \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -v "$VOL_DIR/minio:/minio_data" \
  --health-cmd="curl -f http://localhost:9000/minio/health/live || exit 1" \
  --health-interval=30s --health-timeout=20s --health-retries=3 \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  minio server /minio_data --console-address ":9001"

echo "Waiting for minio..."
while [ "$(docker inspect -f '{{.State.Health.Status}}' milvus-minio 2>/dev/null)" != "healthy" ]; do sleep 3; done
echo "Minio healthy."

echo "Starting Milvus standalone..."
docker rm -f milvus-standalone 2>/dev/null || true
docker run -d \
  --name milvus-standalone \
  --network "$NETWORK_NAME" \
  --network-alias standalone \
  --security-opt seccomp:unconfined \
  -p 19530:19530 \
  -p 9091:9091 \
  -e ETCD_ENDPOINTS=etcd:2379 \
  -e MINIO_ADDRESS=minio:9000 \
  -e MQ_TYPE=woodpecker \
  -v "$VOL_DIR/milvus:/var/lib/milvus" \
  --health-cmd="curl -sf http://localhost:9091/healthz -o /dev/null || exit 1" \
  --health-interval=10s --health-timeout=5s --health-retries=30 --health-start-period=30s \
  milvusdb/milvus:latest \
  milvus run standalone

echo "Waiting for Milvus to become healthy..."
while [ "$(docker inspect -f '{{.State.Health.Status}}' milvus-standalone 2>/dev/null)" != "healthy" ]; do
  sleep 3
done

echo ""
echo "Milvus healthy at localhost:19530"
echo "  MinIO console: http://localhost:9001 (minioadmin/minioadmin)"
