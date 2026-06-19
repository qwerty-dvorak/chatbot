# CLAUDE.md — rag-pipeline

Standalone document ingestion and advanced RAG pipeline. All
LLM/embedding/reranker calls go to local or RunPod endpoints.

## Quick start

```bash
# 1. Clone models to sibling ../models/ repo (one-time, requires git-lfs, ~24GB per model)
bash ../models/clone_models.sh

# 2. Start infrastructure (Milvus + PostgreSQL)
bash ../milvus/start.sh
bash ../db/start.sh

# 3. Start RAG API (local GPU or cloud)
bash run.sh local
bash run.sh runpod              # RunPod cloud GPU

# 4. Queue documents for ingestion and poll the returned job URL
curl -X POST http://localhost:8093/v1/ingest -F "files=@document.pdf"
curl -X POST http://localhost:8093/v1/ingest \
  -F "files=@report.pdf" -F "files=@notes.md" \
  -F "tier=slow" -F "strategy=sentence_window" -F "ocr_mode=paddleocr"
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
bash ../models/deploy-runpod.sh # create RunPod pods + wait for readiness
bash ../tests/rag/test_api.sh   # build rag-api locally, run end-to-end tests
bash ../tests/rag/test_runpod_endpoints.sh  # validate vLLM endpoints against .env.runpod
bash ../models/teardown-runpod.sh           # stop pods + delete templates when done
```

See `TESTING.md` for full details, manual curl examples, and troubleshooting.

## Services (port range 8090+)

| Service | Port | Image | Purpose |
|---------|------|-------|---------|
| rag-text-embed | 8090 | vllm/vllm-openai:latest | nvidia/llama-embed-nemotron-8b text embeddings |
| rag-multimodal-embed | 8091 | vllm/vllm-openai:latest | nvidia/nemotron-colembed-vl-8b-v2 multimodal embeddings |
| rag-reranker | 8092 | vllm/vllm-openai:latest | Qwen3-VL-Reranker-8B pooling via /pooling endpoint |
| rag-api | 8093 | rag-api (ubuntu:24.04) | FastAPI, durable SQLite queue, ingestion worker, search |
| paddleocr-vl | RunPod :8000 or in-process | PaddleOCR-VL-1.6 / PaddleOCR | Image and scanned-page OCR |
| postgres | 5433 | postgres:16 | Shared DB with chatbot-service |
| milvus-standalone | 19530 | milvusdb/milvus:latest | Vector store |

## Architecture

See `docs/architecture.md` for complete topology diagrams including:

- Single-server vs two-server (production) deployment
- Ingestion pipeline with hypothetical question vector indexing
- Search pipeline with query-to-query resolution
- PostgreSQL remote access configuration

```
pipeline/
  config.py                <- Config dataclass loaded from .env
  models.py                <- RawDocument, Chunk, EmbeddedChunk, SearchResult
  extract.py               <- File extraction (PDF, text, images)
  chunk.py                 <- Chunking strategies (recursive, sentence_window, hierarchical)
  embed.py                 <- Text + multimodal embedding via OpenAI-compatible API
  index.py                 <- Milvus indexing + BM25 index
  retrieve.py              <- Vector search, BM25 search, RRF hybrid fusion, reranker
  query.py                 <- Query-time enhancement (HyDE, sub-queries, stepback)
                             AND index-time hypothetical question generation
  ingest.py                <- Ingestion orchestrator
  jobs.py                  <- SQLite job store + single background worker
  tiers.py                 <- Tier policies and promotion
  search.py                <- Search orchestrator (includes query-to-query resolution)
  text_pipeline.py         <- Full text ingest: chunk → hyde → persist → embed → index
  image_pipeline.py        <- Image-only document ingest
  image_preprocess.py      <- EXIF normalization, PNG conversion, vertical splitting
  ocr.py                   <- none/basic Tesseract/PaddleOCR backend selection
```

## Key concepts

### Hypothetical Questions (index-time)

Each chunk generates N hypothetical questions during ingestion. **Each question
is embedded as a separate vector** in Milvus (`chunk_type = hypothetical_question`,
`parent_id` → source chunk). At search time, query-to-query matching resolves
question hits back to source chunks. This is distinct from **HyDE** (query-time
document generation). See `docs/query-enhancements.md` for the full comparison.

### Query-time enhancements

Set `QUERY_ENHANCEMENTS` in `.env`:

| Enhancement | Description |
|-------------|-------------|
| `hyde` | Generate a hypothetical answer and embed it instead of the raw query |
| `sub_queries` | Decompose complex query into 2-4 simpler sub-queries |
| `stepback` | Abstract query to a broader question for better recall |

These are applied at search time. Hypothetical questions (index-time) operate
independently and are always active when `HYPOTHETICAL_QUESTIONS_PER_CHUNK > 0`.

### Two-server deployment

Use a single PostgreSQL instance shared between rag-pipeline and
chatbot-service. Set `POSTGRES_HOST=<server-ip>` in both env files. Ensure
PostgreSQL listens on `*` and permits remote connections.

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
| `hyde` | Query-time: Generate a hypothetical answer and embed it instead of the raw query |
| `sub_queries` | Query-time: Decompose complex query into 2-4 simpler sub-queries |
| `stepback` | Query-time: Abstract query to a broader question for better recall |

The index-time `hypothetical_questions` enhancement is always active when
`HYPOTHETICAL_QUESTIONS_PER_CHUNK > 0`. It is NOT controlled by
`QUERY_ENHANCEMENTS`.

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
POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_PORT
OCR_MODE, OCR_BASE_URL, OCR_MODEL, OCR_MAX_IMAGE_HEIGHT
CHUNK_STRATEGY, CHUNK_SIZE, CHUNK_OVERLAP
QUERY_ENHANCEMENTS, HYPOTHETICAL_QUESTIONS_PER_CHUNK
```
