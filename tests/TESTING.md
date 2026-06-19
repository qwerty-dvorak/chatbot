# Testing Guide

Two testing strategies:
1. **Local GPU** — models run as Docker containers on local GPUs (requires 3x NVIDIA GPUs with 16GB+ VRAM)
2. **RunPod cloud** — models deployed on RunPod cloud GPUs (preferred for development)

---

## Quick Start (any mode)

```bash
# Single command: start full pipeline (reads MODE from .env) + run tests
bash tests/chatbot/test-all.sh

# With keep DB
bash tests/chatbot/test-all.sh --keepdb

# Force mode
bash tests/chatbot/test-all.sh --local --keepdb
bash tests/chatbot/test-all.sh --runpod --keepdb
```

Or step by step:

```bash
# 1. Start full pipeline from root (reads MODE from .env)
bash start.sh

# 2. Run tests against running stack
bash tests/chatbot/test-all.sh --no-start --keepdb
```

Tests run inside the `web` container via `docker exec` — no separate test image,
no mocks, uses the live stack's PostgreSQL, LLM endpoints, and environment variables.

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

## Architecture

Tests run as Django `TestCase` subclasses via `docker exec web uv run python manage.py test`
inside the running `web` container. They connect to the same PostgreSQL, Milvus, and RAG API
that the production stack uses — no separate test image or mocked endpoints.

```
docker exec web
  └─ manage.py test — real PostgreSQL (:5433), real Milvus (:19530)
       ├─ apps.chat.tests      → real LLM endpoint (CHAT_BASE_URL)
       ├─ apps.knowledge.tests → real RAG API (:8093)
       └─ apps.memory.tests    → real Milvus + real LLM embedding

rag-api (FastAPI, port 8093)
  ├─ POST /v1/ingest  → PG queue → background worker → Milvus + BM25
  └─ POST /v1/search  → embed → hybrid retrieve → rerank → results
```

## Test Suites

| Suite | Runner | What it tests |
|-------|--------|---------------|
| chatbot | `bash tests/chatbot/test-all.sh` | Django tests: chat API, memory, RAG context, tools, compaction, knowledge |
| rag | `bash tests/rag/test_api.sh` | RAG API: health, ingest, search (hybrid/vector/bm25), jobs |
| integration | `bash tests/integration/run.sh` | Cross-service: ingest → poll → search → listing |

All tests hit the real LLM endpoint — no mocks or fakes.

## Chatbot Tests

```bash
# Run all chatbot tests (starts full pipeline first, then runs tests inside web container)
bash tests/chatbot/test-all.sh

# With flags
bash tests/chatbot/test-all.sh --runpod --clean        # test then teardown
bash tests/chatbot/test-all.sh --runpod --keepdb       # reuse test database
bash tests/chatbot/test-all.sh --local --keepdb apps.chat.tests
bash tests/chatbot/test-all.sh --runpod --no-start     # stack already running
```

## RAG Pipeline Tests

```bash
# Against a running RAG API
bash tests/rag/test_api.sh

# Validate vLLM endpoints directly
bash tests/rag/test_runpod_endpoints.sh
```

## Integration Tests

```bash
bash tests/integration/run.sh
```

8-step end-to-end test: infrastructure check → RAG API health → upload document → poll job → search chunks → list documents → web UI detail → clean up.

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
## OCR model in test modes

RunPod tests load `OCR_BASE_URL` from `models/.env.runpod` and exercise the
live PaddleOCR-VL endpoint. Local tests use the installed PaddleOCR runtime
when `OCR_MODE=paddleocr`; `OCR_MODE=basic` requires the Tesseract binary in
the RAG API image, and `OCR_MODE=none` verifies image-only indexing.
