# CLAUDE.md — rag-pipeline

Standalone document ingestion and advanced RAG pipeline. All LLM/embedding/reranker calls go to local endpoints (real vLLM servers started via `start-services.sh`).

## Quick start

```bash
# 1. Clone models (one-time, requires git-lfs, ~24GB per model)
bash clone_models.sh

# 2. Start all services (Milvus + vLLM servers + RAG API)
bash start-services.sh
bash start-services.sh --clean   # tear down and restart

# 3. Ingest documents via API
curl -X POST http://localhost:8080/v1/ingest -F "files=@document.pdf"
curl -X POST http://localhost:8080/v1/ingest \
  -F "files=@report.pdf" -F "files=@notes.md" \
  -F "strategy=sentence_window"

# 4. Search via API
curl -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "vector databases", "top_k": 5, "mode": "hybrid"}'

# 5. Browse API docs
open http://localhost:8080/docs
```

## RunPod testing (cloud, no local GPU needed)

```bash
export HF_TOKEN=hf_...          # HuggingFace token (NVIDIA models are gated)
bash runpod_deploy.sh           # create 4 RTX 5090 pods + wait for readiness
bash test_api.sh                # build rag-api locally, run end-to-end tests
bash runpod_teardown.sh         # stop pods + delete templates when done
```

See `TESTING.md` for full details, manual curl examples, and troubleshooting.

## Services

| Service | Port | Image | Purpose |
|---------|------|-------|---------|
| rag-text-embed | 8001 | vllm/vllm-openai:latest | nvidia/llama-embed-nemotron-8b text embeddings |
| rag-multimodal-embed | 8002 | vllm/vllm-openai:latest | nvidia/nemotron-colembed-vl-8b-v2 multimodal embeddings |
| rag-reranker | 8003 | vllm/vllm-openai:latest | Qwen3-VL-Reranker-8B scoring via /score endpoint |
| rag-api | 8080 | rag-api (ubuntu:24.04) | FastAPI ingestion + search service |
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
  search.py                <- Search orchestrator
mock_server/
  server.py                <- 4-port stdlib-only mock server (for local dev/testing)
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/ingest` | Ingest one or more files (multipart form) |
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
