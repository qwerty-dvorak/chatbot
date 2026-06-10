# CLAUDE.md — rag-pipeline

Standalone document ingestion and advanced RAG pipeline. All LLM/embedding/reranker calls go to local endpoints (real vLLM servers started via `start-services.sh`).

## Quick start

```bash
# 1. Clone models (one-time, requires git-lfs, ~24GB per model)
bash clone_models.sh

# 2. Start all services (Milvus + vLLM servers + RAG API)
bash start-services.sh
bash start-services.sh --clean   # tear down and restart

# 3. Queue documents for ingestion and poll the returned job URL
curl -X POST http://localhost:8093/v1/ingest -F "files=@document.pdf"
curl -X POST http://localhost:8093/v1/ingest \
  -F "files=@report.pdf" -F "files=@notes.md" \
  -F "tier=slow" -F "strategy=sentence_window"
curl http://localhost:8093/v1/ingestions/<job-id>

# 4. Search via API
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "vector databases", "top_k": 5, "mode": "hybrid"}'

# 5. Browse API docs
open http://localhost:8093/docs
```

## RunPod testing (cloud, no local GPU needed)

```bash
export HF_TOKEN=hf_...          # HuggingFace token (NVIDIA models are gated)
bash runpod_deploy.sh           # create 4 RTX 5090 pods + wait for readiness
bash test_api.sh                # build rag-api locally, run end-to-end tests
bash runpod_teardown.sh         # stop pods + delete templates when done
```

See `TESTING.md` for full details, manual curl examples, and troubleshooting.

## Services (port range 8090+)

| Service | Port | Image | Purpose |
|---------|------|-------|---------|
| rag-text-embed | 8090 | vllm/vllm-openai:latest | nvidia/llama-embed-nemotron-8b text embeddings |
| rag-multimodal-embed | 8091 | vllm/vllm-openai:latest | nvidia/nemotron-colembed-vl-8b-v2 multimodal embeddings |
| rag-reranker | 8092 | vllm/vllm-openai:latest | Qwen3-VL-Reranker-2B scoring via /score endpoint |
| rag-api | 8093 | rag-api (ubuntu:24.04) | FastAPI, durable SQLite queue, ingestion worker, search |
| milvus-standalone | 19530 | milvusdb/milvus:latest | Vector store |

## Architecture

```
start-services.sh          <- starts all Docker containers
pipeline/
  config.py                <- Config dataclass loaded from .env
  models.py                <- RawDocument, Chunk, EmbeddedChunk, SearchResult
  extract.py               <- File extraction (PDF, text, images)
  chunk.py                 <- Chunking strategies (recursive, sentence_window, hierarchical)
  embed.py                 <- Text + multimodal embedding via OpenAI-compatible API
  index.py                 <- Milvus indexing + BM25 index
  retrieve.py              <- Vector search, BM25 search, RRF hybrid fusion, reranker
  query.py                 <- Query enhancement (HyDE, sub-queries, stepback, hypothetical Qs)
  ingest.py                <- Ingestion orchestrator
  jobs.py                  <- SQLite job store + single background worker
  tiers.py                 <- Tier policies and promotion
  search.py                <- Search orchestrator
mock_server/
  server.py                <- 5-port stdlib-only mock server
  Dockerfile               <- minimal mock image
  start.sh                 <- build/start the mock container
  .env                     <- RAG API configuration for mock endpoints
  test_integration.sh      <- mock + Milvus end-to-end test
```

## Local mock endpoints

Two mock servers provide a complete offline test environment:

### Mock AI Model Server (`mock_server/`)

Matches `docs/runpod_api.md` API shapes:

| Port | Endpoint | Purpose |
|------|----------|---------|
| 9000 | `POST /v1/chat/completions` | General chat/query enhancement |
| 9001 | `POST /v1/embeddings` | Text embedding |
| 9002 | `POST /pooling` | Multimodal ColBERT-style pooling |
| 9003 | `POST /score` | Reranking |
| 9004 | `POST /v1/chat/completions` | PaddleOCR-VL |

### Mock RAG API Server (`mock_rag/`)

Matches `docs/api.md` — all RAG pipeline endpoints on port 8093:

| Endpoint | Description |
|----------|-------------|
| `POST /v1/ingest` | File upload with async ingestion |
| `GET /v1/ingestions` | List jobs |
| `GET /v1/ingestions/{id}` | Poll job |
| `POST /v1/search` | Search |
| `POST /v1/promote` | Tier promotion |
| `GET /health` | Health checks |
| `GET /v1/collections` | List collections |

```bash
# Start all mock services (no GPUs, no downloads)
bash start_mock_all.sh

# Run integration tests
bash mock_server/test_integration.sh
bash mock_rag/test.sh

# Tear down
bash start_mock_all.sh --clean
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/ingest` | Persist uploads and queue ingestion (`202`) |
| `GET` | `/v1/ingestions` | List ingestion and promotion jobs |
| `GET` | `/v1/ingestions/{id}` | Read job status/result |
| `DELETE` | `/v1/ingestions/{id}` | Cancel a queued job |
| `POST` | `/v1/promote` | Queue re-ingestion at a higher tier |
| `POST` | `/v1/search` | Search with optional mode and enhancements |
| `GET` | `/docs` | Interactive API documentation (Swagger UI) |

## Chunking strategies

| Strategy | Description | Best for |
|----------|-------------|----------|
| `recursive` | Parent chunks (2048 chars) split into child chunks (512). Retrieval uses children; context uses parents. | General documents |
| `sentence_window` | One chunk per sentence, with N surrounding sentences as window context. | Dense factual content |
| `hierarchical` | Two-level index: paragraph summaries + fine-grained chunks. | Large document collections |

## Query enhancements (set `QUERY_ENHANCEMENTS` in .env)

| Enhancement | Description |
|-------------|-------------|
| `hyde` | Generate a hypothetical answer and embed it instead of the raw query |
| `sub_queries` | Decompose complex query into 2-4 simpler sub-queries |
| `stepback` | Abstract query to a broader question for better recall |
| `hypothetical_questions` | Index-time only: generate questions per chunk for richer retrieval |

## Adding dependencies

Always use `uv add` — never edit `pyproject.toml` manually:

```bash
cd rag-pipeline
uv add <package>          # adds to pyproject.toml and updates uv.lock
uv add "<package>>=1.2"   # with version constraint
uv add --dev <package>    # dev-only dependency
```

Keep `[tool.uv] exclude-newer = "2025-10-23T12:36:00Z"` in every uv project.

## Key environment variables

See `.env.example` for full list. Critical vars:

```
CHAT_BASE_URL, CHAT_API_KEY, CHAT_MODEL
EMBEDDING_BASE_URL, EMBEDDING_API_KEY, TEXT_EMBEDDING_MODEL, TEXT_EMBEDDING_DIM
MULTIMODAL_EMBEDDING_BASE_URL, MULTIMODAL_EMBEDDING_MODEL, MULTIMODAL_EMBEDDING_DIM
RERANKER_BASE_URL, RERANKER_API_KEY, RERANKER_MODEL
MILVUS_HOST, MILVUS_PORT
CHUNK_STRATEGY, CHUNK_SIZE, CHUNK_OVERLAP
QUERY_ENHANCEMENTS
```
