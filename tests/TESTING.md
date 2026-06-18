# Testing Guide

Two testing strategies:
1. **Local GPU** — models run as Docker containers on local GPUs (requires 3x NVIDIA GPUs with 16GB+ VRAM)
2. **RunPod cloud** — models deployed on RunPod cloud GPUs (preferred for development)

---

## Quick Start (Local GPU)

```bash
# 1. Start infrastructure (PostgreSQL + Milvus)
bash db/start.sh
bash milvus/start.sh

# 2. Deploy local model endpoints (requires 3x GPUs)
bash models/deploy-local.sh

# 3. Start RAG pipeline
bash rag-pipeline/run.sh local

# 4. Start chatbot service
bash chatbot-service/run.sh local

# 5. Run tests
bash tests/run.sh chatbot --local
bash tests/run.sh rag
```

## Quick Start (RunPod)

```bash
export HF_TOKEN=hf_...

# 1. Deploy RunPod pods (huggingface token required for gated models)
bash models/deploy-runpod.sh

# 2. Start infrastructure
bash db/start.sh
bash milvus/start.sh

# 3. Start RAG pipeline
bash rag-pipeline/run.sh runpod

# 4. Start chatbot service
bash chatbot-service/run.sh runpod

# 5. Run tests
bash tests/run.sh chatbot --runpod
bash tests/run.sh rag
```

## Service Ports (Local Deployment)

| Service | Port | Container | Purpose |
|---------|------|-----------|---------|
| Chat LLM (Gemma) | 8430 | gemma-inference | vLLM serving google/gemma-4-26B-A4B-it |
| Text Embed | 8090 | rag-text-embed | nvidia/llama-embed-nemotron-8b |
| Multimodal Embed | 8091 | rag-multimodal-embed | nvidia/nemotron-colembed-vl-8b-v2 |
| Reranker | 8092 | rag-reranker | Qwen3-VL-Reranker-8B |
| RAG API | 8093 | rag-api | FastAPI ingest/search pipeline |
| PostgreSQL | 5433 | chatbot-postgres | Shared DB |
| Milvus | 19530 | milvus-standalone | Vector store |
| File Server | 8888 | file-server | Document browsing |
| Web App | 8080 | web | Django chat UI |

The RAG API (port 8093) handles document ingest and search. The web app (port 8080) provides the chat interface with RAG context injection, user memory, and tool calling.

## Architecture (RunPod)

```
Local machine
  └─ Docker: rag-api (FastAPI, port 8093)
       ├─ POST /v1/ingest  → SQLite queue → background worker → Milvus + BM25
       └─ POST /v1/search  → embed → hybrid retrieve → rerank → results

RunPod (3 pods)
  ├─ rag-text-embed   pod:8000  vLLM + nvidia/llama-embed-nemotron-8b
  ├─ rag-mm-embed     pod:8000  vLLM + nvidia/nemotron-colembed-vl-8b-v2
  └─ rag-reranker     pod:8000  vLLM + Qwen/Qwen3-VL-Reranker-2B
```

## Test Suites

| Suite | Runner | What it tests |
|-------|--------|---------------|
| chatbot | `bash tests/run.sh chatbot` | Django tests: chat API, memory, RAG context, tools, compaction |
| rag | `bash tests/run.sh rag` | RAG API: health, ingest, search (hybrid/vector/bm25), jobs |
| integration | `bash tests/run.sh integration` | Cross-service: chat + RAG + memory (not yet implemented) |
| all | `bash tests/run.sh all` | All suites sequentially |

All tests hit the real LLM endpoint — no mocks or fakes.

## Chatbot Tests

```bash
# Run all chatbot tests (auto-detects RunPod or local from models/.runpod_state)
bash tests/run.sh chatbot

# Force mode
bash tests/run.sh chatbot --runpod
bash tests/run.sh chatbot --local

# With flags
bash tests/run.sh chatbot --runpod --clean        # test then teardown
bash tests/run.sh chatbot --runpod --keepdb       # reuse test database
bash tests/run.sh chatbot --local --keepdb apps.chat.tests
bash tests/run.sh chatbot --runpod --no-start     # stack already running

# Manual (stack already running)
bash tests/chatbot/run-tests.sh
bash tests/chatbot/run-tests.sh --keepdb apps.chat.tests.test_chat_api
```

## RAG Pipeline Tests

```bash
# Against a running RAG API (requires .env.runpod or local .env)
bash tests/rag/test_api.sh

# Validate vLLM endpoints directly
bash tests/rag/test_runpod_endpoints.sh
```

Integration tests: `bash tests/integration/run.sh` (not yet implemented, prints expected flow and exits).

## Testing Individual Model Endpoints

```bash
# Chat (Gemma)
curl -s http://localhost:8430/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"/model","messages":[{"role":"user","content":"hello"}]}'

# Text embedding
curl -s http://localhost:8090/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"/model","input":"hello world"}'

# Multimodal pooling
curl -s http://localhost:8091/pooling \
  -H "Content-Type: application/json" \
  -d '{"model":"/model","input":"hello world"}'

# Reranker score
curl -s http://localhost:8092/score \
  -H "Content-Type: application/json" \
  -d '{"model":"/model","text_1":"query","text_2":"document"}'
```

## Manual RAG API Testing

```bash
# Health check
curl http://localhost:8093/health

# Queue a document for ingestion
job_id=$(curl -sS -X POST http://localhost:8093/v1/ingest \
  -F "files=@sample_data/sample.pdf" -F "tier=instant" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Poll job status
curl "http://localhost:8093/v1/ingestions/$job_id"

# Search
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"vector databases","mode":"hybrid","top_k":3}'
```

## Troubleshooting

### Pod stays in non-RUNNING state (RunPod)
```bash
runpodctl pod get <pod-id>
runpodctl pod list --all
```

### vLLM model download takes too long
Re-run `bash models/deploy-runpod.sh` — it skips pods already healthy.

### Milvus health check fails
```bash
docker logs milvus-standalone | tail -20
curl -sf http://localhost:9091/healthz
```

### PostgreSQL connection refused
```bash
docker logs chatbot-postgres | tail -20
docker exec chatbot-postgres runuser -u postgres -- pg_isready
```

## Cost Estimate (RunPod)

| Pod | GPU | ~$/hr |
|-----|-----|-------|
| rag-milvus | CPU | ~$0.10 |
| rag-text-embed | H100 SXM | ~$3.00 |
| rag-mm-embed | H100 SXM | ~$3.00 |
| rag-reranker | H100 SXM | ~$3.00 |
| **Total** | | **~$9.10/hr** |

Model download (~30 min) + testing (~10 min) ≈ **~$6 total**.
