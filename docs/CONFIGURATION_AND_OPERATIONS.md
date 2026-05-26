# Configuration And Operations

## Dependency Policy

The current `pyproject.toml` contains:

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "django>=5.1.2",
    "litellm==1.40.0",
]
```

Use `uv` only:

- `uv sync`
- `uv run python manage.py ...`
- `uv lock`
- `uv add ...` when adding approved dependencies

Do not introduce `pip`, `poetry`, npm, yarn, pnpm, or Node-based build steps.

Expected dependency groups:

- runtime: Django, psycopg, LiteLLM, pymilvus, file parsing libraries.
- dev: pytest, pytest-django, ruff or equivalent if allowed.
- optional OCR/PDF/image dependencies based on accepted file formats.

No npm dependency group is needed.

## Docker Compose Runtime

Local development and deployment should run through Docker Compose.

Expected services:

```text
postgres        PostgreSQL with pgvector
milvus          Vector store (document chunks, memories)
etcd            Milvus metadata store
minio           Milvus object storage
web             Django ASGI/WSGI app, started with uv
worker          Python background worker, started with uv
scheduler       optional periodic worker, started with uv
litellm         optional LiteLLM proxy if not embedded in web/worker
```

Compose requirements:

- mount source code for local development,
- persist PostgreSQL data in a named volume,
- persist Milvus data in a named volume,
- persist media uploads in a named volume or local bind mount,
- run migrations through `uv run python manage.py migrate`,
- never run npm install or a JavaScript build container.

## Environment Variables

Recommended `.env` keys:

```env
DJANGO_SETTINGS_MODULE=config.settings.local
SECRET_KEY=change-me
DEBUG=true
ALLOWED_HOSTS=localhost,127.0.0.1

DATABASE_URL=postgresql://chatbot:chatbot@localhost:5432/chatbot

MEDIA_ROOT=./media
STATIC_ROOT=./staticfiles

# Model endpoints (via LiteLLM)
LITELLM_BASE_URL=http://localhost:8000/v1
LITELLM_API_KEY=local-placeholder

# Chat model: Gemma 4 26B A4B IT
CHAT_MODEL=gemma-4-26b-a4b-it
VISION_MODEL=gemma-4-26b-a4b-it

# Text embedding: nvidia/llama-embed-nemotron-8b (dim: 4096)
TEXT_EMBEDDING_MODEL=nvidia/llama-embed-nemotron-8b
TEXT_EMBEDDING_DIM=4096

# Multimodal embedding: nvidia/nemotron-colembed-vl-8b-v2 (dim: 4096)
MULTIMODAL_EMBEDDING_MODEL=nvidia/nemotron-colembed-vl-8b-v2
MULTIMODAL_EMBEDDING_DIM=4096

# Reranker: Qwen3-VL-Reranker-8B
RERANKER_MODEL=Qwen/Qwen3-VL-Reranker-8B

# Milvus vector store
MILVUS_HOST=localhost
MILVUS_PORT=19530

CHAT_CONTEXT_MAX_TOKENS=32000
CHAT_CONTEXT_MAX_TOKENS_LARGE=256000
CHAT_RESPONSE_MAX_TOKENS=4096
CHAT_COMPACTION_THRESHOLD_TOKENS=14000
CHAT_STREAMING_ENABLED=true

RAG_TOP_K=8
RAG_MIN_SIMILARITY=0.45
RAG_CHUNK_TARGET_TOKENS=700
RAG_CHUNK_OVERLAP_TOKENS=120

MAX_UPLOAD_MB=50
INGESTION_SYNC=false
KNOWLEDGE_DEFAULT_VISIBILITY=private
MEMORY_AUTO_SAVE_DEFAULT=true

TOOL_CALLS_ENABLED=true
TOOL_CALL_TIMEOUT_SECONDS=60
TOOL_RESULT_MAX_TOKENS=4000
```

## PostgreSQL Setup

Database requirements:

- PostgreSQL version compatible with selected Django and psycopg versions.
- Database user allowed to create databases in local development.

The vector extension is no longer needed since Milvus handles all vector operations.

Initial database setup should include:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

Standard B-tree indexes for chat, message, user, and job lookup paths.

## Milvus Setup

Milvus replaces pgvector for all vector storage and search.

Collections are auto-created by `apps/llm/milvus_store.ensure_collections()`.

Two collections:

| Collection | Dimension | Metric | Purpose |
|-----------|-----------|--------|---------|
| `document_chunks` | 4096 | IP | Document chunk retrieval |
| `user_memories` | 4096 | IP | User memory search |

Milvus requires three services in Docker Compose:
- `milvus` (main service)
- `etcd` (metadata store)
- `minio` (object storage for Milvus)

## Model Serving

Each model has a dedicated server in the `scripts/` directory:

| Directory | Model | Port | Purpose |
|-----------|-------|------|---------|
| `scripts/gemma4/` | Gemma 4 26B A4B IT | 8001 | Chat/vision (GGUF via llama.cpp) |
| `scripts/llama-embed-nemotron/` | llama-embed-nemotron-8b | 8002 | Text embeddings |
| `scripts/nemotron-colembed/` | nemotron-colembed-vl-8b-v2 | 8003 | Multimodal embeddings |
| `scripts/qwen3-reranker/` | Qwen3-VL-Reranker-8B | 8004 | Re-ranking |

All model servers expose OpenAI-compatible endpoints.

## LiteLLM And Local Gemma 4

LiteLLM should be wrapped behind local project code instead of called directly from views.

Recommended wrapper responsibilities:

- model name mapping,
- request timeout,
- retry policy,
- structured logging,
- token accounting,
- streaming event handling,
- tool call parsing,
- error normalization,
- provider-specific compatibility fixes.

The local model runtime should expose an OpenAI-compatible API:

```text
POST /v1/chat/completions
POST /v1/embeddings
POST /v1/rerank
```

The selected chat/vision model is `Gemma 4 26B A4B IT` with 256K context window.
Text embedding: `nvidia/llama-embed-nemotron-8b` (dim: 4096).
Multimodal embedding: `nvidia/nemotron-colembed-vl-8b-v2` (dim: 4096).
Reranker: `Qwen3-VL-Reranker-8B`.

## Management Commands

Recommended commands:

```text
python manage.py ingest_path <path>
python manage.py retry_ingestion_jobs
python manage.py run_ingestion_worker
python manage.py reembed_documents
python manage.py compact_chat <chat_id>
python manage.py rebuild_memory_embeddings
python manage.py rag_search "query text"
python manage.py list_tools
python manage.py sync_builtin_tools
```

These keep local operations Python-only and avoid a separate service stack.

## Background Jobs

Workers are allowed. Start with the simplest Python-only worker that satisfies local needs:

- synchronous processing for tiny local uploads where useful,
- management-command worker for queued jobs,
- scheduler container for periodic jobs,
- Celery/RQ only if dependency policy allows it.

Worker responsibilities:

- document ingestion,
- image/PDF analysis,
- embedding generation,
- chat compaction,
- memory extraction,
- safe retry of failed jobs,
- long-running tool calls.

## Testing Plan

Minimum tests:

- custom user creation and login,
- chat creation and message storage,
- context builder token budget behavior,
- memory retrieval filtering by user,
- document chunking,
- vector retrieval access control,
- ingestion failure and retry,
- compaction does not delete messages,
- LiteLLM wrapper handles provider errors.
- streaming stores deltas and finalizes messages correctly.
- tool calls are validated, permission checked, executed, and recorded.
- public share links do not expose private knowledge sources.

Use fake LLM, fake embedding, and fake reranker clients in tests so tests do not require the local model runtime.

## Security Notes

- Never store model API keys in the database.
- Validate all uploads by MIME type and size.
- Store file hashes to deduplicate and audit uploads.
- Do not send private user documents into global retrieval contexts.
- Enforce ownership filters before retrieval ranking.
- Enforce tool permissions before every tool execution.
- Store tool arguments and results, but redact secrets before persistence.
- Do not put secrets into memories.
- Log login events and sensitive setting changes.
- Keep `DEBUG=false` outside local development.

## Operational Metrics

Track:

- ingestion jobs queued/running/failed,
- average ingestion duration,
- number of ready documents,
- number of chunks in Milvus,
- retrieval latency,
- chat completion latency,
- token usage by user/model/operation,
- failed LLM calls,
- compaction count per chat,
- tool calls by tool/status/user,
- tool execution latency and failure rate,
- stream failures and cancelled generations.
