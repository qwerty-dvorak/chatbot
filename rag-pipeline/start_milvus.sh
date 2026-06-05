#!/bin/bash

# 0. Clean up any existing containers from previous runs
echo "Cleaning up old containers..."
docker rm -f milvus-standalone milvus-minio milvus-etcd 2>/dev/null

# 1. Set volume directory using absolute path (defaults to current directory if not set)
VOL_DIR="${DOCKER_VOLUME_DIRECTORY:-$(pwd)}"

# 2. Create the custom Docker network (ignores error if it already exists)
echo "Creating network 'milvus'..."
docker network inspect milvus >/dev/null 2>&1 || docker network create milvus

# 3. Start ETCD
echo "Starting etcd..."
docker run -d \
  --name milvus-etcd \
  --network milvus \
  --network-alias etcd \
  -e ETCD_AUTO_COMPACTION_MODE=revision \
  -e ETCD_AUTO_COMPACTION_RETENTION=1000 \
  -e ETCD_QUOTA_BACKEND_BYTES=4294967296 \
  -e ETCD_SNAPSHOT_COUNT=50000 \
  -v "${VOL_DIR}/volumes/etcd:/etcd" \
  --health-cmd="etcdctl endpoint health" \
  --health-interval=30s \
  --health-timeout=20s \
  --health-retries=3 \
  quay.io/coreos/etcd:v3.7.0-rc.0 \
  etcd -advertise-client-urls=http://etcd:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

# 4. Start MinIO
echo "Starting minio..."
docker run -d \
  --name milvus-minio \
  --network milvus \
  --network-alias minio \
  -p 9001:9001 \
  -p 9000:9000 \
  -e MINIO_ACCESS_KEY=minioadmin \
  -e MINIO_SECRET_KEY=minioadmin \
  -v "${VOL_DIR}/volumes/minio:/minio_data" \
  --health-cmd="curl -f http://localhost:9000/minio/health/live || exit 1" \
  --health-interval=30s \
  --health-timeout=20s \
  --health-retries=3 \
  minio/minio:RELEASE.2024-12-18T13-15-44Z \
  minio server /minio_data --console-address ":9001"

# 5. Wait for dependencies
echo "Waiting 10 seconds for etcd and minio to initialize..."
sleep 10

# 6. Start Milvus Standalone
echo "Starting Milvus standalone..."
docker run -d \
  --name milvus-standalone \
  --network milvus \
  --network-alias standalone \
  --security-opt seccomp:unconfined \
  --gpus '"device=0"' \
  -p 19530:19530 \
  -p 9091:9091 \
  -e ETCD_ENDPOINTS=etcd:2379 \
  -e MINIO_ADDRESS=minio:9000 \
  -e MQ_TYPE=woodpecker \
  -v "${VOL_DIR}/volumes/milvus:/var/lib/milvus" \
  milvusdb/milvus:v3.0-beta-gpu-amd64 \
  milvus run standalone

echo "Milvus cluster deployment initiated successfully!"
