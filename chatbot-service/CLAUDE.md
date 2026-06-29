# CLAUDE.md — chatbot-service

Django chatbot with streaming LLM responses, RAG, per-user memory, and tool calling.
See root `CLAUDE.md` for overall setup. See `docs/` for architecture details.

## Commands

```bash
# Start the full pipeline from root (reads MODE from .env)
bash ../start.sh

# Start chatbot-service only (uses models/.env.local for endpoints)
bash run.sh local

# Start with RunPod cloud GPU (uses models/.env.runpod)
bash run.sh runpod

# Start with mock LLM (no GPU required)
bash run.sh no-gemma

# Run all chatbot tests (starts full stack first, then runs tests inside web container)
bash ../tests/chatbot/test-all.sh                     # detect mode
bash ../tests/chatbot/test-all.sh --keepdb            # reuse test DB
bash ../tests/chatbot/test-all.sh --keepdb apps.chat.tests  # specific app

# If stack is already running, skip startup:
bash ../tests/chatbot/test-all.sh --no-start
bash ../tests/chatbot/test-all.sh --no-start --keepdb apps.chat.tests.test_chat_api

# Sync built-in tool definitions into DB
docker exec web uv run python manage.py sync_builtin_tools --settings=config.settings.production

# Toggle every repository Dockerfile between public and BARC sources
bash ../configure-build-sources.sh apply
bash ../configure-build-sources.sh revert
```

## Adding dependencies

Always use `uv add` inside `chatbot-service/` — never edit `pyproject.toml` manually:

```bash
cd chatbot-service
uv add <package>          # adds to pyproject.toml and updates uv.lock
uv add "<package>>=1.2"   # with version constraint
uv add --dev <package>    # dev-only dependency
```

Keep `[tool.uv] exclude-newer = "2025-10-23T12:36:00Z"` in every uv project.

## Architecture

No npm/Node.js — pure Django templates with server-rendered HTML and native `EventSource` for SSE streaming.

**Settings modules**: `config.settings.local` (dev), `config.settings.production`, `config.settings.test`.

**Vector storage**: Milvus for user memory embeddings; PostgreSQL for relational data (shared with rag-pipeline).

**LLM abstraction**: All model calls via `ChatClient` in `apps/llm/clients.py`. Three endpoint pairs:
`CHAT_BASE_URL/CHAT_API_KEY`, `EMBEDDING_BASE_URL/EMBEDDING_API_KEY`, `RERANKER_BASE_URL/RERANKER_API_KEY`.

### Django Apps

| App | Responsibility |
|-----|---------------|
| `accounts` | UUID-based User model, email login, SecurityLog |
| `chat` | Chat, Message, streaming handler, ChatShare, Vote |
| `memory` | Per-user Memory facts with Milvus embeddings |
| `knowledge` | KnowledgeSource, Document, DocumentChunk, hybrid search |
| `ingestion` | IngestionJob queue, file extraction, chunking, embedding |
| `llm` | Chat client, embeddings, Milvus vector store, SSE streaming |
| `tools` | ToolDefinition registry, ToolCall/ToolExecution audit trail |
| `compaction` | Summarizes old messages when context exceeds threshold |

### Key Environment Variables

Copy `.env.example` to `.env`:

```
RAG_API_ENABLED, RAG_API_BASE_URL
CHAT_BASE_URL, CHAT_API_KEY, CHAT_MODEL, VISION_MODEL
LORA_ADAPTERS
EMBEDDING_BASE_URL, EMBEDDING_API_KEY
RERANKER_BASE_URL, RERANKER_API_KEY
MILVUS_HOST, MILVUS_PORT
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT
RAG_ENABLED, RAG_TOP_K, RAG_MIN_SIMILARITY
TOOL_CALLS_ENABLED
```

### RAG Pipeline Integration

Document upload and search can be offloaded to the standalone RAG pipeline
(`rag-pipeline/`) on a separate server:

```
RAG_API_ENABLED=true
RAG_API_BASE_URL=http://<rag-server-ip>:8093
```

Knowledge uploads and chat messages accept multiple attachments. Knowledge
uploads expose `ocr_mode=none|basic|paddleocr`; image-derived rows, OCR status,
preprocessing, hashes, and text are stored in shared PostgreSQL.

When enabled, `apps/knowledge/rag_client.py` handles all ingest and search via
the RAG Pipeline API.  Visibility maps to tier: `private` → instant,
`shared` → slow, `global` → global tier.

The RAG pipeline and chatbot service share the same PostgreSQL database. The
rag-pipeline writes document chunks; the chatbot-service reads them for
display and metadata queries.  The chatbot-service connects to the shared
Milvus for user memory storage only (document vectors are managed by the
rag-pipeline).

When `RAG_API_ENABLED=false`, the chatbot-service handles its own document
ingestion and search using the Django `knowledge` and `ingestion` apps with
local Milvus. This is the legacy standalone mode.

**LLM-routed RAG context injection** (`apps/chat/context.py`): before answering,
one structured LLM call decides whether the message needs RAG and selects query
enhancements. Only a positive decision triggers `/v1/search`. `@document`
mentions are resolved by the application, removed from semantic query text, and
passed as `artifact_sources` so retrieval is restricted to the requested files.
The latest explicit document selection remains active for follow-up turns until
another explicit selector replaces or clears it. Scoped searches combine dense,
BM25, and direct lexical chunk matching so structural references and rare
needle facts in large documents are not lost to semantic ranking.
`rag.search` is context-only and is never exposed as a callable tool to the
answer model. Retrieved content and filenames are injected into the system
prompt before the response is generated.

## Testing

All tests hit the real LLM endpoint — no mocks or fakes. Tests run inside the
running `web` container via `docker exec`, using the live stack's PostgreSQL,
LLM endpoints, and environment variables. No separate test image.

### Quick start

```bash
# From root: start full pipeline, then run all tests
bash ../start.sh
bash ../tests/chatbot/test-all.sh --no-start --keepdb

# Single command: start pipeline + run tests
bash ../tests/chatbot/test-all.sh --keepdb

# Specific app with keep DB
bash ../tests/chatbot/test-all.sh --no-start --keepdb apps.chat.tests

# Pass Django test labels:
bash ../tests/chatbot/test-all.sh --keepdb apps.chat.tests.test_chat_api

# Force mode
bash ../tests/chatbot/test-all.sh --local --keepdb
bash ../tests/chatbot/test-all.sh --runpod --keepdb
```

The runner auto-detects mode (runpod/local) from the web container's
`CHAT_BASE_URL` env var.

### Running without a real LLM (no GPU required, limited test coverage)

```bash
bash run.sh no-gemma
```
