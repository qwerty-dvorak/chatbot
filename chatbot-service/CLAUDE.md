# CLAUDE.md — chatbot-service

Django chatbot with streaming LLM responses, RAG, per-user memory, and tool calling.
See root `CLAUDE.md` for overall setup. See `docs/` for architecture details.

## Commands

```bash
# Start all chatbot services (builds image, starts postgres, file-server, gemma inference, web, worker)
bash start-services.sh

# Run migrations (inside running container)
docker exec web uv run python manage.py migrate --settings=config.settings.production

# Run all tests (inside container)
docker exec web uv run python manage.py test --settings=config.settings.test

# Run tests for a single app
docker exec web uv run python manage.py test apps.chat --settings=config.settings.test

# Sync built-in tool definitions into DB
docker exec web uv run python manage.py sync_builtin_tools --settings=config.settings.production

# Test RAG retrieval from CLI
docker exec web uv run python manage.py rag_search "query string" --settings=config.settings.production

# Build standalone Docker image (PostgreSQL embedded)
docker build -t chatbot . && docker run -p 8000:8000 chatbot

# Toggle every repository Dockerfile between public and BARC sources
cd ..
bash configure-build-sources.sh apply
bash configure-build-sources.sh revert
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

**Vector storage**: Milvus for embeddings; PostgreSQL for relational data.

**LLM abstraction**: All model calls via LiteLLM in `apps/llm/clients.py`. Three endpoint pairs:
`CHAT_BASE_URL/CHAT_API_KEY`, `EMBEDDING_BASE_URL/EMBEDDING_API_KEY`, `RERANKER_BASE_URL/RERANKER_API_KEY`.

### Django Apps

| App | Responsibility |
|-----|---------------|
| `accounts` | UUID-based User model, email login, SecurityLog |
| `chat` | Chat, Message, streaming handler, ChatShare, Vote |
| `memory` | Per-user Memory facts with Milvus embeddings |
| `knowledge` | KnowledgeSource, Document, DocumentChunk, hybrid search |
| `ingestion` | IngestionJob queue, file extraction, chunking, embedding |
| `llm` | LiteLLM client, embeddings, Milvus vector store, SSE streaming |
| `tools` | ToolDefinition registry, ToolCall/ToolExecution audit trail |
| `compaction` | Summarizes old messages when context exceeds threshold |

### Key Environment Variables

Copy `.env.example` to `.env`:

```
CHAT_BASE_URL, CHAT_API_KEY
EMBEDDING_BASE_URL, EMBEDDING_API_KEY
RERANKER_BASE_URL, RERANKER_API_KEY
CHAT_MODEL, TEXT_EMBEDDING_MODEL, MULTIMODAL_EMBEDDING_MODEL, RERANKER_MODEL
MILVUS_HOST, MILVUS_PORT
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT
RAG_ENABLED, RAG_TOP_K, RAG_MIN_SIMILARITY
TOOL_CALLS_ENABLED
```

### RAG Pipeline Integration

Document upload can be offloaded to the standalone RAG pipeline (`rag-pipeline/`):

```
RAG_API_ENABLED=true
RAG_API_BASE_URL=http://localhost:8093
```

When enabled, `apps/knowledge/rag_client.py` handles ingest and search via the RAG API.
Visibility maps to tier: `private` → instant, `shared` → slow, `global` → global tier.
