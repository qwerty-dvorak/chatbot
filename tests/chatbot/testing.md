# Chatbot Service Tests

Django tests against a live stack (PostgreSQL + Milvus + LLM endpoint). No mocks or fakes.

## Quick Start

```bash
# Start infrastructure first
bash db/start.sh
bash milvus/start.sh

# Run tests (auto-detects RunPod or local mode)
bash ../tests/run.sh chatbot
bash ../tests/run.sh chatbot --runpod
bash ../tests/run.sh chatbot --local

# Specific apps with keepdb
bash ../tests/run.sh chatbot --runpod --keepdb apps.chat.tests
bash ../tests/run.sh chatbot --local --keepdb apps.chat.tests.test_chat_api
```

## Manual (stack already running)

```bash
bash tests/chatbot/run-tests.sh
bash tests/chatbot/run-tests.sh --keepdb apps.chat.tests
```

## Files

| File | Purpose |
|------|---------|
| `run-tests.sh` | Build test image + run Django tests against running stack |
| `test-all.sh` | Orchestrator: start services, wait, run tests, optionally clean up |
| `Dockerfile` | Test image based on ubuntu:24.04 with uv, no postgresql inside |
| `test-with-runpod.sh` | RunPod-specific test launcher |
| `test_mock.sh` | Tests against mock LLM |

## Environment Variables

| Var | Required | Default |
|-----|----------|---------|
| `CHAT_BASE_URL` | Yes | `http://gemma-inference-server:8000/v1` |
| `CHAT_API_KEY` | No | `dummy` |
| `CHAT_MODEL` | No | `openai/google/gemma-4-E4B-it` |
| `POSTGRES_HOST` | Yes | `chatbot-postgres` |
| `POSTGRES_PORT` | Yes | `5433` |
