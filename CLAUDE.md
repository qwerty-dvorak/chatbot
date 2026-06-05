# CLAUDE.md — Monorepo Root

Everything in this repository runs **fully locally** with no external dependencies.
No model weights are fetched automatically. No cloud APIs are called. No internet access is required at runtime.

## Structure

| Directory | Purpose |
|-----------|---------|
| `chatbot-service/` | Django chatbot with streaming LLM, RAG, memory, tool calling |
| `rag-pipeline/` | Standalone document ingestion + advanced RAG pipeline |

## Requirements

- Docker 24+ with NVIDIA Container Toolkit
- 3x NVIDIA GPUs with 16GB+ VRAM each (one per model: text embed, multimodal embed, reranker)
- Python 3.12 (only needed for developing inside containers)

## Key principles

- **No external model downloads** — all LLM / embedding / reranker calls go to local endpoints (real vLLM or mock servers).
- **No Docker Compose** — services are started with plain `docker run` commands or native processes.
- **Python images** always use `FROM ubuntu:24.04` as base; dependencies managed with `uv`.
- **Milvus** is the vector store, started via `rag-pipeline/start-services.sh`.
- **PostgreSQL 16** runs embedded inside the chatbot-service Docker image.
- **Adding Python dependencies** — always use `uv add <package>` inside the relevant subdirectory (`chatbot-service/` or `rag-pipeline/`). Never edit `pyproject.toml` or `uv.lock` manually.

## Quick start

```bash
# 1. Clone models (requires git-lfs, ~24GB per model)
cd rag-pipeline && bash clone_models.sh

# 2. Start all services (Milvus + vLLM embedding/reranker servers + RAG API)
cd rag-pipeline && bash start-services.sh

# 3. Ingest documents (via API)
curl -X POST http://localhost:8080/v1/ingest -F "files=@/path/to/doc.pdf"

# 4. Search
curl -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "your question", "top_k": 5}'

# 5. Start chatbot (separate services)
cd chatbot-service && bash start-services.sh
```
