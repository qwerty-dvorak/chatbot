# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Dev server (requires PostgreSQL — set POSTGRES_* env vars or use docker-compose)
uv run python manage.py runserver

# Ingestion worker (required for document processing)
uv run python manage.py run_ingestion_worker --interval 5

# Migrations
uv run python manage.py migrate

# Run all tests
uv run python manage.py test --settings=config.settings.test

# Run tests for a single app
uv run python manage.py test apps.chat --settings=config.settings.test

# Sync built-in tool definitions into the DB
uv run python manage.py sync_builtin_tools

# Test RAG retrieval from the CLI
uv run python manage.py rag_search "query string"

# Ingest files from a local path
uv run python manage.py ingest_path /path/to/files

# Docker Compose (PostgreSQL + web + worker; Milvus disabled — see docker-compose.yml)
docker-compose up

# Standalone single-container deployment (PostgreSQL embedded)
docker build -t chatbot . && docker run -p 8000:8000 chatbot
```

## Architecture

This is a Django chatbot with streaming LLM responses, RAG, per-user memory, and tool calling. No npm/Node.js — pure Django templates with server-rendered HTML and native `EventSource` for SSE streaming.

**Settings modules**: `config.settings.local` (PostgreSQL, default for dev), `config.settings.production` (PostgreSQL), `config.settings.test` (PostgreSQL test DB). All settings load `.env` via `python-dotenv` (`manage.py` and `wsgi.py` both call `load_dotenv()`). See `docs/RUNNING.md` for full setup steps.

**Vector storage**: Milvus holds embeddings for document chunks (`document_chunks` collection) and user memories (`user_memories` collection). PostgreSQL holds all relational data.

**LLM abstraction**: All model calls go through `apps/llm/clients.py` via LiteLLM. Three separate endpoint pairs: `CHAT_BASE_URL/CHAT_API_KEY`, `EMBEDDING_BASE_URL/EMBEDDING_API_KEY`, `RERANKER_BASE_URL/RERANKER_API_KEY`. Model names must use the `openai/` prefix (e.g. `openai/gemma-4-26b`) when targeting custom endpoints.

### Django Apps

| App | Responsibility |
|-----|---------------|
| `accounts` | UUID-based User model with email login; SecurityLog |
| `chat` | Chat, Message, MessageDelta, MessageAttachment, ChatShare, Vote; streaming handler |
| `memory` | Per-user Memory facts with Milvus embeddings; MemorySettings |
| `knowledge` | KnowledgeSource, Document, DocumentChunk; hybrid search and reranking |
| `ingestion` | IngestionJob queue; file extraction (PDF/text/image), chunking, embedding, Milvus indexing |
| `llm` | LiteLLM client, embeddings, Milvus vector store, SSE streaming primitives |
| `tools` | ToolDefinition registry, ToolCall/ToolExecution/ToolResult audit trail, permission grants |
| `compaction` | ChatCompaction; summarizes old messages when context exceeds threshold |

### Request Flow (Chat)

1. User submits message → `apps/chat/views.py` creates Message record
2. `apps/chat/context.py` assembles context: recent messages + compaction summary + memory retrieval + RAG chunks + attachments
3. LLM call via `apps/llm/clients.py`; streaming deltas saved as `MessageDelta` records
4. Tool calls are intercepted, dispatched through `apps/tools/executor.py`, results appended
5. Response streamed to browser via SSE (`EventSource`)

### Context Assembly (`apps/chat/context.py`)

Order of injection into system prompt:
1. ChatCompaction summary (if chat exceeds `CHAT_COMPACTION_THRESHOLD_TOKENS`, default 14,000)
2. Recent messages only (not full history)
3. Relevant Memory records from Milvus similarity search
4. Relevant DocumentChunks via hybrid search (vector + BM25) with optional reranking
5. MessageAttachments from current message

### Tool System (`apps/tools/`)

Built-in tools registered via `sync_builtin_tools`: `rag.search`, `memory.search`, `memory.save`, `chat.compact`, `knowledge.ingest_status`, `document.analyze`. Each `ToolDefinition` has a JSON schema, permission level (user/staff/admin/system), and optional `ToolPermissionGrant` per user.

### Ingestion Pipeline (`apps/ingestion/`)

`IngestionJob` records are polled by `run_ingestion_worker`. Processing: extract text/images → chunk → embed via `TEXT_EMBEDDING_MODEL` or `MULTIMODAL_EMBEDDING_MODEL` → index into Milvus → update `Document.status` (pending→processing→ready/failed).

### Mock server (`mock_server/server.py`)

Stdlib-only OpenAI-compatible server for local development. Serves chat completions (with `reasoning_content` deltas and tool-call streaming) and embeddings on configurable ports.

```bash
# Two ports (default): chat on :9000, embeddings on :9001
python mock_server/server.py

# Single port for both
python mock_server/server.py --port 9000

# Options: --chat-port, --embed-port, --embed-dim, --chat-model, --embed-model
```

### Key Environment Variables

Copy `.env.example` to `.env`. Critical vars:

```
# Separate endpoints for chat and embedding models
CHAT_BASE_URL, CHAT_API_KEY          # vLLM chat/completion endpoint
EMBEDDING_BASE_URL, EMBEDDING_API_KEY  # vLLM embedding endpoint

CHAT_MODEL, TEXT_EMBEDDING_MODEL, MULTIMODAL_EMBEDDING_MODEL, RERANKER_MODEL
MILVUS_HOST, MILVUS_PORT
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT
CHAT_COMPACTION_THRESHOLD_TOKENS
RAG_ENABLED           # feature flag — set false to disable RAG and hide rag.*/knowledge.* tools
RAG_TOP_K, RAG_MIN_SIMILARITY
TOOL_CALLS_ENABLED
```

### Documentation

Detailed architecture and data model docs are in `docs/` (ARCHITECTURE.md, DATA_MODEL.md, FILE_STRUCTURE.md).
