# RAG Pipeline — RunPod Testing Guide

End-to-end testing of the RAG ingestion and search API using 4 RunPod pods.

## Architecture

```
Local machine
  └─ Docker: rag-api (FastAPI, port 8080)
       ├─ POST /v1/ingest  → text/multimodal embed → Milvus + BM25
       └─ POST /v1/search  → embed → hybrid retrieve → rerank → results

RunPod (4 pods, all RTX 5090)
  ├─ rag-milvus      port 19530/TCP  milvusdb/milvus:v3.0-beta-gpu-amd64
  ├─ rag-text-embed  port 8000/HTTP  vllm/vllm-openai + nvidia/llama-embed-nemotron-8b
  ├─ rag-mm-embed    port 8000/HTTP  vllm/vllm-openai + nvidia/nemotron-colembed-vl-8b-v2
  └─ rag-reranker    port 8000/HTTP  vllm/vllm-openai + Qwen/Qwen3-VL-Reranker-8B
```

The RAG API runs locally via Docker and connects to the RunPod services over the internet.
vLLM pods are accessed via RunPod's HTTPS proxy (`https://<pod-id>-8000.proxy.runpod.net`).
Milvus is accessed via the pod's public IP on TCP port 19530 (gRPC).

## Prerequisites

- `runpodctl` installed and authenticated (`runpodctl doctor`)
- Docker installed locally
- HuggingFace account with licenses accepted for:
  - https://huggingface.co/nvidia/llama-embed-nemotron-8b
  - https://huggingface.co/nvidia/nemotron-colembed-vl-8b-v2
  - Qwen models are open-weight (no license required)
- Sample PDF at `data/sample_data/2025-0910-newsletter.pdf`

## Quick start (local mock server — verified working)

```bash
cd rag-pipeline

# 1. Start local Milvus (etcd + minio + milvus containers)
bash runpod_start_local_milvus.sh

# 2. Start mock server (ports 9000-9003: chat, text-embed, mm-embed, reranker)
python3 mock_server/server.py &

# 3. Run end-to-end tests (builds rag-api locally, tests all 7 endpoints)
bash test_api.sh      # uses .env.runpod which points to localhost mock + Milvus

# 4. Clean up
bash runpod_start_local_milvus.sh --clean
kill %1 2>/dev/null || true
```

## Quick start (RunPod GPU pods — see note below)

```bash
cd rag-pipeline
export HF_TOKEN=hf_...
bash runpod_deploy.sh        # deploys 3 vLLM pods + local Milvus
bash test_api.sh
bash runpod_teardown.sh
```

> **Note on RunPod GPU compatibility**: `vllm/vllm-openai:latest` crash-loops on RTX 5090
> (Blackwell architecture, compute capability 10.0) because the image is compiled for older
> CUDA compute capabilities. Use A100/H100 GPUs which have well-supported CUDA kernels.
> The intended production models (`nvidia/llama-embed-nemotron-8b`,
> `nvidia/nemotron-colembed-vl-8b-v2`) also use custom architectures not natively supported
> by vLLM; they require a different serving stack (e.g., sentence-transformers server or TEI).
> For RunPod tests, swap to `BAAI/bge-large-en-v1.5` + `BAAI/bge-reranker-v2-m3` which
> are BERT/XLM-RoBERTa models that vLLM natively supports.

## Step-by-step breakdown

### runpod_deploy.sh

Creates 4 RunPod pods and templates:

| Pod | GPU | Image | Purpose |
|-----|-----|-------|---------|
| rag-milvus | CPU | milvusdb/milvus:v3.0-beta-amd64 | Vector store (etcd + minio bundled in startup script) |
| rag-text-embed | RTX 5090 | vllm/vllm-openai:latest | `BAAI/bge-large-en-v1.5` embeddings (1024-dim) |
| rag-mm-embed | RTX 5090 | vllm/vllm-openai:latest | `BAAI/bge-large-en-v1.5` embeddings (testing fallback) |
| rag-reranker | RTX 5090 | vllm/vllm-openai:latest | `BAAI/bge-reranker-v2-m3` scoring via `/score` |

Pod IDs are saved to `.runpod_state`. Use `--no-wait` to skip the readiness wait:
```bash
bash runpod_deploy.sh --no-wait   # deploy only
bash runpod_wait.sh               # wait separately (useful if you disconnect)
```

### runpod_wait.sh

Runs in phases:
1. **RUNNING** — waits for all 4 pods to reach `RUNNING` state (~2-5 min)
2. **Connection info** — extracts proxy URLs for vLLM pods; gets Milvus public IP
3. **Model readiness** — polls `/health` on each vLLM pod until 200 (model download: ~15-30 min per pod)
4. **Milvus gRPC** — verifies TCP port 19530 is accepting connections
5. **Embedding dims** — hits `/v1/embeddings` to detect actual output dimensions
6. Writes `.env.runpod` with all connection details

### test_api.sh

Builds the `rag-api` Docker image locally, starts it with `.env.runpod`, then runs 7 tests:

| Test | Endpoint | What it validates |
|------|----------|-------------------|
| 1 | GET /health | API started, Milvus connected |
| 2 | GET /v1/collections | Collections endpoint reachable |
| 3 | POST /v1/ingest | PDF extraction → chunking → embedding → Milvus + BM25 index |
| 4 | GET /v1/collections | Collection created after ingest |
| 5 | POST /v1/search (hybrid) | Vector + BM25 RRF fusion + reranking |
| 6 | POST /v1/search (vector) | Pure vector search |
| 7 | POST /v1/search (bm25) | Pure BM25 search |

## Manual testing

After `runpod_wait.sh` completes, build and start the API manually:

```bash
# Build
docker build -t rag-api .

# Start (data dir mounted for BM25 index persistence)
docker run -d \
  --name rag-api \
  --env-file .env.runpod \
  -v "$PWD/data:/app/data" \
  -p 8080:8080 \
  rag-api

# Health check
curl http://localhost:8080/health

# Ingest the sample PDF
curl -X POST http://localhost:8080/v1/ingest \
  -F "files=@data/sample_data/2025-0910-newsletter.pdf" \
  -F "strategy=recursive"

# Ingest with sentence_window chunking
curl -X POST http://localhost:8080/v1/ingest \
  -F "files=@data/sample_data/2025-0910-newsletter.pdf" \
  -F "strategy=sentence_window"

# Hybrid search with reranking
curl -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what are the key highlights", "top_k": 5, "mode": "hybrid", "use_reranker": true}'

# Vector-only search
curl -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "main announcements", "top_k": 5, "mode": "vector", "use_reranker": false}'

# BM25 search
curl -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "newsletter updates", "top_k": 5, "mode": "bm25", "use_reranker": false}'

# List collections
curl http://localhost:8080/v1/collections

# API docs
open http://localhost:8080/docs
```

## Troubleshooting

### Pod stays in non-RUNNING state
```bash
runpodctl pod get <pod-id>   # check status and error fields
runpodctl pod list --all     # list all pods including exited
```

### vLLM model download takes too long
Models are ~16 GB each. On RunPod, download speed varies. If `runpod_wait.sh` times out (30 min default per model), re-run it — it will skip pods that are already healthy.

### Milvus public IP not detected
After the pod is RUNNING, run:
```bash
runpodctl pod get <MILVUS_POD_ID>
```
Find the IP in the output and set it manually:
```bash
sed -i 's/MILVUS_HOST=.*/MILVUS_HOST=<actual-ip>/' .env.runpod
```

### Wrong embedding dimensions
If Milvus rejects inserts with a dimension mismatch, check the actual dims:
```bash
source .runpod_state
# Text embed dim
curl -s -X POST "$TEXT_URL/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-embed-nemotron-8b","input":"test"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))"

# Update .env.runpod with correct dim
sed -i "s/TEXT_EMBEDDING_DIM=.*/TEXT_EMBEDDING_DIM=<actual>/" .env.runpod
```

### Why BGE models instead of the NVIDIA/Qwen models?

`nvidia/llama-embed-nemotron-8b` uses a `LlamaBidirectionalModel` architecture and `nvidia/nemotron-colembed-vl-8b-v2` uses `Qwen3VLNemotronEmbedModel` — neither is supported by `vllm/vllm-openai:latest`. These models require a custom serving framework (e.g., sentence-transformers server). For RunPod testing, `BAAI/bge-large-en-v1.5` and `BAAI/bge-reranker-v2-m3` are used as drop-in vLLM-compatible alternatives. Swap back to the intended models when using the local `start-services.sh` workflow (which runs the models natively without vLLM).

### Reranker returns 404 on /score
The reranker pod may not yet support the `/score` endpoint (vLLM version mismatch). The pipeline falls back to `/v1/rerank` automatically.

### Check running costs
```bash
runpodctl billing pods
```
All 4 RTX 5090 pods are billed per hour. Run `bash runpod_teardown.sh` when done.

## Cost estimate

| Pod | GPU | ~$/hr (community) |
|-----|-----|-------------------|
| rag-milvus | CPU | ~$0.10 |
| rag-text-embed | RTX 5090 | ~$1.00 |
| rag-mm-embed | RTX 5090 | ~$1.00 |
| rag-reranker | RTX 5090 | ~$1.00 |
| **Total** | | **~$4.00/hr** |

Model download phase (~30 min) + testing (~10 min) ≈ **~$2-3 total**.
