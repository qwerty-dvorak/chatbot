# RAG Pipeline — Testing Guide

Two testing strategies:
1. **Mock servers** (no GPUs, no downloads, works offline) — for CI and daily dev
2. **RunPod GPU pods** — for end-to-end validation against real vLLM models

---

## Quick Start (Mock Servers — Verified Working)

```bash
cd rag-pipeline

# Start all mock services (AI model server + RAG API + Milvus):
bash start_mock_all.sh

# Run the full integration test suite:
bash mock_server/test_integration.sh

# Or test only the mock RAG API:
bash mock_rag/test.sh

# Stop everything:
bash start_mock_all.sh --clean
```

This starts three services:

| Service | Port | What it mocks |
|---------|------|---------------|
| `mock_server` (ai-models) | 9000-9004 | 5 vLLM endpoints: chat, text embed, multimodal pooling, reranker, OCR |
| `mock_rag` (rag-api) | 8093 | Full RAG Pipeline API: ingest, search, promote, health, collections |
| Milvus standalone | 19530 | Vector store (etcd + minio bundled) |

The mock model server is a **combined** mock AI model server that matches the
API shapes in `docs/runpod_api.md`. All 5 AI services run in one container
on ports 9000-9004, each implementing the exact request/response format of the
corresponding RunPod vLLM pod.

The mock RAG API implements all endpoints from `docs/api.md` including async
ingestion (simulated with background threads), search, promote, health checks,
and collection management.

## Mock Testing Details

### Mock AI Model Server (`mock_server/server.py`)

Stdlib-only (zero dependencies). Runs 5 HTTP server threads:

| Port | Endpoint | RunPod equivalent |
|------|----------|-------------------|
| 9000 | `POST /v1/chat/completions` | General chat/query enhancement LLM |
| 9001 | `POST /v1/embeddings` | Text embedding (nvidia/llama-embed-nemotron-8b) |
| 9002 | `POST /pooling` | Multimodal ColBERT-style pooling |
| 9003 | `POST /score` | Reranker (Qwen/Qwen3-VL-Reranker-2B) |
| 9004 | `POST /v1/chat/completions` | PaddleOCR-VL (OCR) |

### Mock RAG API (`mock_rag/server.py`)

Stdlib-only (zero dependencies). Exposes:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health`, `/health/live`, `/health/ready` | Health checks |
| `POST` | `/v1/ingest` | File upload + async ingestion (202) |
| `GET` | `/v1/ingestions` | List jobs |
| `GET` | `/v1/ingestions/{id}` | Poll job status |
| `DELETE` | `/v1/ingestions/{id}` | Cancel queued job |
| `POST` | `/v1/promote` | Queue tier promotion |
| `POST` | `/v1/search` | Synchronous search |
| `GET` | `/v1/collections` | List Milvus collections |
| `DELETE` | `/v1/collections/{name}` | Drop collection |

### What the mock tests validate

The integration test (`mock_server/test_integration.sh`) validates:
1. Health endpoints respond 200
2. Collections listing works (pre and post ingest)
3. File upload queues an ingestion job
4. Job polling detects completion
5. Job listing returns history
6. Search works in hybrid, vector, and BM25 modes
7. Promote endpoint accepts requests
8. Readiness check reports status

### Using mock servers for chatbot-service development

The chatbot-service `apps/knowledge/rag_client.py` connects to the RAG API
when `RAG_API_ENABLED=true`. For local development:

```bash
# In chatbot-service/.env or .env.mock:
RAG_API_ENABLED=true
RAG_API_BASE_URL=http://localhost:8093
```

---

## Quick Start (RunPod GPU Pods)

```bash
cd rag-pipeline
export HF_TOKEN=hf_...   # HuggingFace token for gated NVIDIA models

bash runpod_deploy.sh               # deploy 3 vLLM pods + local Milvus
bash test_api.sh                    # build rag-api locally, run end-to-end tests
bash runpod_teardown.sh             # stop pods + delete templates when done
```

> **Note on GPU compatibility**: RTX 5090 (Blackwell, CC 10.0) is not fully
> supported by `vllm/vllm-openai:latest`. Use H100/H100 NVL for reliable testing.
> See "Why BGE models" below for alternatives.

## Architecture (RunPod)

```
Local machine
  └─ Docker: rag-api (FastAPI, port 8093)
       ├─ POST /v1/ingest  → SQLite queue → background worker → Milvus + BM25
       └─ POST /v1/search  → embed → hybrid retrieve → rerank → results

RunPod (4 pods)
  ├─ milvus           (local Docker)
  ├─ rag-text-embed   pod:8000/HTTP  vLLM + nvidia/llama-embed-nemotron-8b
  ├─ rag-mm-embed     pod:8000/HTTP  vLLM + nvidia/nemotron-colembed-vl-8b-v2
  └─ rag-reranker     pod:8000/HTTP  vLLM + Qwen/Qwen3-VL-Reranker-2B
```

## Step-by-step (RunPod)

### deploy

```bash
bash runpod_deploy.sh
```

Creates 3 RunPod pods + local Milvus:

| Pod | GPU | Image | Purpose |
|-----|-----|-------|---------|
| rag-milvus | — | milvusdb/milvus:latest | Local Docker (not on RunPod) |
| rag-text-embed | H100 SXM | vllm/vllm-openai:latest | `nvidia/llama-embed-nemotron-8b` |
| rag-mm-embed | H100 SXM | vllm/vllm-openai:latest | `nvidia/nemotron-colembed-vl-8b-v2` |
| rag-reranker | H100 SXM | vllm/vllm-openai:latest | `Qwen/Qwen3-VL-Reranker-2B` |

Use `--no-wait` to deploy without waiting:
```bash
bash runpod_deploy.sh --no-wait
bash runpod_wait.sh               # wait separately
```

### wait

`runpod_wait.sh` does:
1. Waits for pods to reach `RUNNING` (~2-5 min)
2. Extracts proxy URLs + Milvus IP
3. Polls `/health` on each vLLM pod until 200 (model download: ~15-30 min)
4. Detects actual embedding dimensions via live API
5. Writes `.env.runpod`

### test

```bash
bash test_api.sh
```

Builds `rag-api` Docker image locally, starts it with `.env.runpod`, runs 8 tests:

| Test | Endpoint | What it validates |
|------|----------|-------------------|
| 1 | GET /health | API started |
| 2 | GET /v1/collections | Collections reachable |
| 3 | POST /v1/ingest + poll | Full pipeline: upload → extract → embed → index |
| 4 | GET /v1/ingestions | Job history |
| 5 | GET /v1/collections | Collection created |
| 6 | POST /v1/search (hybrid) | Vector + BM25 + rerank |
| 7 | POST /v1/search (vector) | Pure vector |
| 8 | POST /v1/search (bm25) | Pure BM25 |

### teardown

```bash
bash runpod_teardown.sh
```

Stops local Milvus, deletes all pods and templates, removes `.runpod_state` and `.env.runpod`.

## Manual testing (RunPod)

After `runpod_wait.sh` completes:

```bash
docker build -t rag-api .
docker run -d --name rag-api --network host --env-file .env.runpod \
  -v "$PWD/data:/app/data" rag-api

curl http://localhost:8093/health

job_id=$(curl -sS -X POST http://localhost:8093/v1/ingest \
  -F "files=@data/sample_data/2025-0910-newsletter.pdf" -F "tier=instant" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl "http://localhost:8093/v1/ingestions/$job_id"

curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"newsletter highlights","mode":"hybrid","top_k":3}'
```

## Testing individual model endpoints

The mock server also doubles as a target for testing individual model API shapes:

```bash
# Text embedding (matches runpod_api.md)
curl -s http://localhost:9001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-embed-nemotron-8b","input":"hello world"}'

# Multimodal pooling
curl -s http://localhost:9002/pooling \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/nemotron-colembed-vl-8b-v2","input":"hello world"}'

# Reranker score
curl -s http://localhost:9003/score \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-VL-Reranker-2B","text_1":"query","text_2":"document"}'

# OCR
curl -s http://localhost:9004/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"PaddlePaddle/PaddleOCR-VL-1.6","messages":[{"role":"user","content":[{"type":"text","text":"OCR:"}]}]}'
```

## Troubleshooting

### Pod stays in non-RUNNING state
```bash
runpodctl pod get <pod-id>
runpodctl pod list --all
```

### vLLM model download takes too long
Re-run `runpod_wait.sh` — it skips pods already healthy.

### Milvus public IP not detected
```bash
runpodctl pod get <MILVUS_POD_ID>
sed -i 's/MILVUS_HOST=.*/MILVUS_HOST=<actual-ip>/' .env.runpod
```

### Why BGE models in RunPod tests?
`nvidia/llama-embed-nemotron-8b` uses `LlamaBidirectionalModel` and
`nvidia/nemotron-colembed-vl-8b-v2` uses `Qwen3VLNemotronEmbedModel` —
neither is supported by `vllm/vllm-openai:latest`. For RunPod tests, swap to
`BAAI/bge-large-en-v1.5` and `BAAI/bge-reranker-v2-m3` which are vLLM-compatible.
When using the local `start-services.sh` workflow (which runs models natively
without vLLM), use the intended NVIDIA/Qwen models.

### Reranker returns 404 on /score
vLLM version mismatch. The pipeline falls back to `/v1/rerank` automatically.

### Mock tests fail
```bash
# Check container logs
docker logs rag-mock-server
docker logs rag-mock-api
docker logs test-milvus

# Verify ports are listening
curl -v http://localhost:9001/v1/embeddings -d '{"input":"test"}'
curl -v http://localhost:8093/health
```

## Cost estimate (RunPod)

| Pod | GPU | ~$/hr |
|-----|-----|-------|
| rag-milvus | CPU | ~$0.10 |
| rag-text-embed | H100 SXM | ~$3.00 |
| rag-mm-embed | H100 SXM | ~$3.00 |
| rag-reranker | H100 SXM | ~$3.00 |
| **Total** | | **~$9.10/hr** |

Model download (~30 min) + testing (~10 min) ≈ **~$6 total**.
