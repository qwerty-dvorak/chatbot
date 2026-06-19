# Integration Tests

Cross-service tests that exercise the full RAG pipeline (ingest → index → search).

## Prerequisites

The full stack must be running:

```bash
bash start.sh                    # starts everything (based on root .env MODE)
```

Or start services individually:

```bash
bash db/start.sh
bash milvus/start.sh
bash rag-pipeline/run.sh runpod  # or "local"
bash chatbot-service/run.sh runpod  # or "local" or "no-gemma"
```

## Running

```bash
# Auto-detect mode from running stack
bash tests/integration/run.sh
```

## What It Tests

| # | Test | Description |
|---|------|-------------|
| 1 | RAG API health | GET /health returns ok |
| 2 | Ingest tier=global | Upload astronomy doc for global tier |
| 3 | Ingest tier=instant | Upload factual text for private/instant tier |
| 4 | Ingest tier=slow | Upload biology doc for shared/slow tier |
| 5 | Job completion | Poll first job until succeeded |
| 6 | Hybrid search | Search for astronomy content |
| 7 | Job listing | List all ingestion jobs |
| 8 | Web UI reachable | GET /knowledge/ returns 200 or 302 |

## Architecture

```
test script ──HTTP──→ RAG API (:8093)  ──→ Milvus (:19530)
                │                      ──→ PostgreSQL (:5433)
                │
                └──→ Chatbot Web UI (:8080)
```
