# Running the Chatbot

## Requirements

- Python 3.12+ with `uv` installed (`pip install uv`)
- Docker (for PostgreSQL and optional services)
- No Node.js required

---

## Quick Start (local dev with mock LLM)

This uses the built-in mock server instead of a real model, so you can develop and test the UI without any GPU.

### 1. Copy and configure `.env`

```bash
cp .env.example .env
```

For local dev with the mock server the defaults are already set correctly — the key settings are:

```
CHAT_BASE_URL=http://localhost:9000/v1
EMBEDDING_BASE_URL=http://localhost:9000/v1
RERANKER_BASE_URL=http://localhost:9000/v1
CHAT_MODEL=openai/mock-chat
TEXT_EMBEDDING_MODEL=openai/mock-embed
RAG_ENABLED=false
```

> **Note:** LiteLLM requires model names to be prefixed with `openai/` when pointing at a custom OpenAI-compatible endpoint.

### 2. Start PostgreSQL

```bash
docker run -d --name chatbot-postgres \
  -p 5432:5432 \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  postgres:16-alpine
```

Wait for it to be ready:

```bash
docker exec chatbot-postgres pg_isready -U chatbot -d chatbot
```

### 3. Install dependencies

```bash
uv sync
```

### 4. Run migrations and seed tools

```bash
uv run python manage.py migrate
uv run python manage.py sync_builtin_tools
```

### 5. Create a superuser

```bash
uv run python manage.py createsuperuser
```

Or non-interactively:

```bash
uv run python manage.py shell -c "
from apps.accounts.models import User
User.objects.create_superuser(email='admin@local.dev', password='admin')
"
```

### 6. Start the mock LLM server

```bash
python mock_server/server.py --port 9000
```

Runs on `http://localhost:9000/v1`. Serves:
- `POST /v1/chat/completions` — streaming with `reasoning_content` deltas and tool-call support
- `POST /v1/embeddings` — deterministic unit-vector embeddings
- `GET /v1/models`

To use separate ports for chat and embeddings (matching `.env.example` defaults):

```bash
python mock_server/server.py --chat-port 9000 --embed-port 9001
```

Options:
```
--port           single port for both chat and embeddings
--chat-port      port for chat completions (default: 9000)
--embed-port     port for embeddings (default: 9001)
--embed-dim      embedding vector dimension (default: read from TEXT_EMBEDDING_DIM env, else 4096)
--chat-model     model name to advertise (default: mock-chat)
--embed-model    model name to advertise (default: mock-embed)
```

### 7. Start the Django dev server

In a separate terminal:

```bash
uv run python manage.py runserver 8080
```

Open `http://localhost:8080` — you'll be redirected to the login page.

---

## Running Tests

Tests require a live PostgreSQL instance. Set `POSTGRES_TEST_DB` to use a separate test database (default: `chatbot_test`).

```bash
# All tests
uv run python manage.py test --settings=config.settings.test

# Single app
uv run python manage.py test apps.chat --settings=config.settings.test

# Single test class
uv run python manage.py test apps.chat.tests.test_chat_flow.ChatFlowTest --settings=config.settings.test
```

---

## Docker Compose

The compose setup uses the official `postgres:16-alpine` image and builds the app from the unified `Dockerfile` (PostgreSQL 16 + uv + Django).

Milvus, etcd, and MinIO are **commented out** while `RAG_ENABLED=false`. To re-enable the vector store, uncomment those services in `docker-compose.yml` and set `RAG_ENABLED=true` in `.env`.

```bash
# Build and start everything (postgres, web, worker)
docker-compose up --build

# Start only postgres, run Django locally
docker-compose up postgres
```

Services:
| Service     | Port  | Description |
|-------------|-------|-------------|
| postgres    | 5432  | PostgreSQL 16 (official postgres:16-alpine) |
| mock        | 9000  | Mock LLM server — chat completions with tool calls |
| mock        | 9001  | Mock LLM server — embeddings |
| file-server | 8888  | Local file server — documents organised by user/date |
| web         | 8000  | Django + gunicorn |
| worker      | —     | Ingestion background worker |

Persistent volumes: `postgres_data`, `media_data`, `docs_data`.

---

## Standalone Single-Container Deployment

The `Dockerfile` is a unified image that bundles PostgreSQL 16 alongside the Django app. When `POSTGRES_HOST` is `localhost` (the default), the entrypoint initialises and starts an embedded PostgreSQL cluster before handing off to gunicorn. This lets you ship and run the entire stack as a single container with no external dependencies.

```bash
# Build
docker build -t chatbot .

# Run (all defaults: postgres on localhost, gunicorn on :8000)
docker run -d \
  -p 8000:8000 \
  -v chatbot_pgdata:/var/lib/postgresql/data \
  -v chatbot_media:/app/media \
  chatbot

# Custom credentials / settings
docker run -d \
  -p 8000:8000 \
  -e POSTGRES_DB=mydb \
  -e POSTGRES_USER=myuser \
  -e POSTGRES_PASSWORD=secret \
  -v chatbot_pgdata:/var/lib/postgresql/data \
  -v chatbot_media:/app/media \
  chatbot
```

> **docker-compose vs standalone:** When `POSTGRES_HOST` is set to anything other than `localhost`/`127.0.0.1` (e.g. `postgres` in docker-compose), the embedded PostgreSQL is skipped and the app connects to the external host.

---

## Connecting to Real vLLM Endpoints

Update `.env` to point at your running vLLM instances:

```env
CHAT_BASE_URL=http://<chat-server>:8000/v1
CHAT_API_KEY=your-api-key

EMBEDDING_BASE_URL=http://<embed-server>:8000/v1
EMBEDDING_API_KEY=your-api-key

RERANKER_BASE_URL=http://<embed-server>:8000/v1
RERANKER_API_KEY=your-api-key

# Model names must use openai/ prefix with custom endpoints
CHAT_MODEL=openai/gemma-4-26b-a4b-it
TEXT_EMBEDDING_MODEL=openai/nvidia/llama-embed-nemotron-8b
TEXT_EMBEDDING_DIM=4096
MULTIMODAL_EMBEDDING_MODEL=openai/nvidia/nemotron-colembed-vl-8b-v2
MULTIMODAL_EMBEDDING_DIM=4096
RERANKER_MODEL=openai/Qwen/Qwen3-VL-Reranker-8B

# Enable RAG once Milvus and the embedding model are available
RAG_ENABLED=true
MILVUS_HOST=localhost
MILVUS_PORT=19530
```

---

## Management Commands

```bash
# Sync built-in tool definitions to the database (run after migrations)
uv run python manage.py sync_builtin_tools

# List all registered tools
uv run python manage.py list_tools

# Ingest files from a local path
uv run python manage.py ingest_path /path/to/files

# Start the document ingestion worker (long-running)
uv run python manage.py run_ingestion_worker --interval 5

# Retry failed ingestion jobs
uv run python manage.py retry_ingestion_jobs

# Re-embed all document chunks (e.g. after changing embedding model/dim)
uv run python manage.py reembed_documents

# Test RAG retrieval from the CLI
uv run python manage.py rag_search "your query here"

# Collect static files
uv run python manage.py collectstatic --noinput
```

---

## Tool Calls

Tool calls are enabled by default in the docker-compose stack. The mock server picks the correct tool from the registered list based on keywords in the user's message.

### Triggering tools from the chat UI

Type any of the following in a chat message:

| What you type | Tool invoked |
|---|---|
| `Search my memory for Python` | `memory.search` |
| `What do you remember about me?` | `memory.search` |
| `Remember that I prefer dark mode` | `memory.save` |
| `Search the knowledge base for climate` | `rag.search` |
| `What's the ingestion status?` | `knowledge.ingest_status` |
| `Analyze my latest document` | `document.analyze` |

### What happens in the UI

1. You send a message → Django creates a PENDING assistant message
2. Browser opens `EventSource` to `/chats/<id>/stream/`
3. Mock LLM streams a `tool_calls` finish — you'll see a **🔧 tool_name(args)** block appear
4. Django executes the tool against the real database (memory, knowledge chunks, etc.)
5. A **tool result** is shown under the tool block
6. Django calls the mock LLM again with the tool result → it streams a plain text follow-up

### Enabling/disabling individual tools

Go to **Settings** (`/settings/`) and use the toggle next to each tool name.

---

## Local Document Storage

Files uploaded through the **Knowledge** page are stored locally — no cloud buckets.

Path structure:
```
data/docs/<user-id>/<YYYY-MM-DD>/knowledge/<filename>
```

The `file-server` service provides HTTP access to these files:
```bash
# List all files
curl http://localhost:8888/browse

# List files for a specific user
curl http://localhost:8888/browse?user=<user-uuid>

# Download a file
curl http://localhost:8888/files/<user>/<date>/knowledge/<filename>
```

The `docs_data` Docker volume is shared between `file-server`, `web`, and `worker`.

---

## Troubleshooting

**`LLM Provider NOT provided`** — Model name is missing the `openai/` prefix. Set `CHAT_MODEL=openai/<model-name>`.

**`peer unexpectedly closed connection`** — The LLM server is not running or returned an invalid HTTP response. Check that the mock server (or vLLM) is up on the configured port.

**`connection refused` on port 5432** — PostgreSQL is not running. Start the Docker container from step 2.

**Settings not updating after editing `.env`** — The server reads `.env` at startup via `python-dotenv`. Restart the Django process after any `.env` change.

**`CSRF token missing`** — Browser cookies must be enabled. The login session cookie is required for all POST requests.
