#!/bin/bash
# Start a minimal local Milvus standalone for use with RunPod testing.
# Milvus data is ephemeral (stored in /tmp/milvus-runpod-test).
# Run this before test_api.sh when MILVUS_HOST=localhost in .env.runpod.
#
# Usage: bash runpod_start_local_milvus.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NET="milvus_runpod_test"
VOL="/tmp/milvus-runpod-test"

mkdir -p "$VOL/etcd" "$VOL/minio" "$VOL/milvus"

cleanup_flag="${1:-}"
if [[ "$cleanup_flag" == "--clean" ]]; then
  echo "Stopping and removing test Milvus containers..."
  docker rm -f test-milvus test-minio test-etcd 2>/dev/null || true
  docker network rm "$NET" 2>/dev/null || true
  exit 0
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

echo "Starting etcd..."
docker rm -f test-etcd 2>/dev/null || true
docker run -d --name test-etcd --network "$NET" --network-alias etcd \
  -v "$VOL/etcd:/etcd" \
  -e ETCD_AUTO_COMPACTION_MODE=revision \
  -e ETCD_AUTO_COMPACTION_RETENTION=1000 \
  -e ETCD_QUOTA_BACKEND_BYTES=4294967296 \
  -e ETCD_SNAPSHOT_COUNT=50000 \
  quay.io/coreos/etcd:v3.7.0-rc.0 \
  etcd -advertise-client-urls=http://etcd:2379 \
       -listen-client-urls=http://0.0.0.0:2379 \
       --data-dir=/etcd

echo "Starting minio..."
docker rm -f test-minio 2>/dev/null || true
docker run -d --name test-minio --network "$NET" --network-alias minio \
  -v "$VOL/minio:/minio_data" \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  server /minio_data --console-address ":9001"

echo "Waiting 8s for etcd+minio..."
sleep 8

echo "Starting Milvus standalone (v3.0-beta-amd64)..."
docker rm -f test-milvus 2>/dev/null || true
# --user root: milvus image runs as non-root by default; mounted volume is
# owned by the host user, so we force root to avoid permission denied errors.
docker run -d --name test-milvus --network "$NET" \
  --user root \
  -p 19530:19530 \
  -v "$VOL/milvus:/var/lib/milvus" \
  -e ETCD_ENDPOINTS=etcd:2379 \
  -e MINIO_ADDRESS=minio:9000 \
  -e MQ_TYPE=woodpecker \
  milvusdb/milvus:v3.0-beta-amd64 \
  milvus run standalone

echo ""
echo "Waiting 15s for Milvus to initialize..."
sleep 15

# Verify port is open
if timeout 5 bash -c ">/dev/tcp/localhost/19530" 2>/dev/null; then
  echo "Milvus is ready at localhost:19530"
else
  echo "WARNING: Milvus port not yet open — it may still be starting"
  echo "Wait a few more seconds then re-check: nc -z localhost 19530"
fi

echo ""
echo "Stop with: bash runpod_start_local_milvus.sh --clean"
