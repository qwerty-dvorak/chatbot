# CLAUDE.md — chatbot-service

Django chatbot with streaming LLM responses, RAG, per-user memory, and tool calling.
See root `CLAUDE.md` for overall setup. See `docs/` for architecture details.

## Commands

```bash
# Start all chatbot services (builds image, starts postgres, file-server, gemma inference, web, worker)
bash start-services.sh

# Start with mock LLM (no GPU required)
bash start-services-no-gemma.sh

# Start with RunPod cloud GPU (no local GPU needed)
# First deploy the model on RunPod, then:
bash start-services-with-runpod.sh

# Run all tests (auto-detects RunPod or local; starts stack if needed)
bash test-all.sh                          # detect mode
bash test-all.sh --runpod                 # use RunPod GPU
bash test-all.sh --local                  # use local GPU
bash test-all.sh --runpod --clean         # test then teardown
bash test-all.sh --runpod --keepdb apps.chat.tests  # specific app

# Run tests against an already-running stack
bash run-tests.sh
bash run-tests.sh --keepdb apps.chat.tests.test_chat_api

# Sync built-in tool definitions into DB
docker exec web uv run python manage.py sync_builtin_tools --settings=config.settings.production

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

**Vector storage**: Milvus for user memory embeddings; PostgreSQL for relational data (shared with rag-pipeline).

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
RAG_API_ENABLED, RAG_API_BASE_URL
CHAT_BASE_URL, CHAT_API_KEY, CHAT_MODEL, VISION_MODEL
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

**Auto RAG context injection** (`apps/chat/context.py`): every chat message
triggers a RAG search.  Results are injected into the system prompt so the LLM
always has relevant knowledge context.  When `RAG_API_ENABLED=true`, search
goes to the RAG Pipeline API (`/v1/search`) which returns actual document
content with filenames.  Fallback is a local `icontains` search on
`DocumentChunk`.

## Testing

All tests hit the real LLM endpoint — no mocks or fakes. Tests run against a
live RunPod pod (`google/gemma-4-E4B-it`) or a local GPU (`gemma4-26B-A4b`).

Use the unified orchestrator to start the stack + run tests in one command:

```bash
bash test-all.sh                          # auto-detect mode
bash test-all.sh --runpod                 # RunPod cloud GPU
bash test-all.sh --runpod --clean         # test then teardown
bash test-all.sh --local                  # local GPU
bash test-all.sh --local --clean          # test then teardown

# Pass Django test labels:
bash test-all.sh --runpod --keepdb apps.chat.tests
bash test-all.sh --runpod --keepdb apps.chat.tests.test_chat_api

# If the stack is already running, skip startup:
bash test-all.sh --runpod --no-start
bash test-all.sh --local --no-start --keepdb apps.chat.tests.test_context
```

### Legacy test runner (requires running stack)

```bash
bash run-tests.sh
bash run-tests.sh --keepdb
bash run-tests.sh apps.chat.tests
```

The test image (`Dockerfile.test`) connects to the stack's PostgreSQL
on the `chatbot_net` Docker network. The runner:
1. Verifies the stack is running
2. Grants CREATEDB to the chatbot DB user
3. Builds `chatbot-test` image
4. Runs the container on `chatbot_net` with DB + LLM endpoint env vars

### RunPod Cloud GPU Setup

Deploy the chat LLM on a RunPod GPU, then test:

```bash
# Terminal 1: deploy (15-30 min for model download)
export HF_TOKEN=hf_...
bash runpod_deploy_chat.sh

# Terminal 2: start stack + run tests (or use bash test-all.sh --runpod)
bash start-services-with-runpod.sh
bash test-all.sh --runpod --no-start

# Stop the RunPod pod when done
bash runpod_teardown_chat.sh
```

The deploy script creates one RTX 4090 (or override with `GPU_ID=...`) pod running
vLLM with `google/gemma-4-E4B-it` and `--reasoning-parser gemma4`.

### Local GPU Setup

Requires 2x NVIDIA GPUs with 16GB+ VRAM:

```bash
# Clone model files first
bash ../rag-pipeline/clone_models.sh

# Start the full stack (builds image, starts gemma inference, postgres, web, worker)
bash start-services.sh
bash test-all.sh --local --no-start
```

### Mock Stack (no GPU required, limited test coverage)

Some tests that don't require the LLM can run with the mock server:

```bash
# 1. Start all RAG pipeline mock services
cd ../rag-pipeline && bash start_mock_all.sh

# 2. Start chatbot service (builds image, starts mock server + postgres + web + worker)
cd ../chatbot-service && bash start-services-no-gemma.sh

# Creates:
#   chatbot-mock-server  — chat (9000), embed (9001), reranker (9003) on chatbot_net
#   postgres             — port 5433
#   file-server          — port 8888
#   web                  — port 8080
#   worker               — ingestion worker
```
