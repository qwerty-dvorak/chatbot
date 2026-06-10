# CLAUDE.md — Monorepo Root

Everything in this repository runs **fully locally** with no external dependencies.
No cloud APIs are called. No internet access is required at runtime.

## Structure

| Directory | Purpose |
|-----------|---------|
| `chatbot-service/` | Django chatbot with streaming LLM, RAG, memory, tool calling |
| `rag-pipeline/` | Standalone document ingestion + advanced RAG pipeline |

## Models

Model files live in a **separate git repo** at the same level as this repo (`../models/`).
They are mounted as Docker volumes at runtime — never copied into this repo.
See `rag-pipeline/start-services.sh` and `chatbot-service/start-services.sh` for mount paths.

```
barc/
├── chatbot/          ← this repo
└── models/           ← separate git repo (HF model configs, tokenizers, safetensors)
    ├── gemma-4-26B-A4B-it/
    ├── llama-embed-nemotron-8b/
    ├── nemotron-colembed-vl-8b-v2/
    └── Qwen3-VL-Reranker-2B/
```

To clone models locally:
```bash
bash rag-pipeline/clone_models.sh
```

## Requirements

- Docker 24+ with NVIDIA Container Toolkit
- 3x NVIDIA GPUs with 16GB+ VRAM each (one per model: text embed, multimodal embed, reranker)
- Python 3.12 (only needed for developing inside containers)

## Key principles

- **Models outside repo** — all model files live in a sibling `../models/` git repo; cloned once via `rag-pipeline/clone_models.sh`. Docker volumes mount them at runtime.
- **No Docker Compose** — services are started with plain `docker run` commands or native processes.
- **Python images** always use `FROM ubuntu:24.04` as base; dependencies managed with `uv`.
- **Reproducible uv resolution** — every `pyproject.toml` contains `[tool.uv]` with `exclude-newer = "2025-10-23T12:36:00Z"`.
- **Milvus** is the vector store, started via `rag-pipeline/start-services.sh`.
- **PostgreSQL 16** runs embedded inside the chatbot-service Docker image.
- **Adding Python dependencies** — always use `uv add <package>` inside the relevant subdirectory (`chatbot-service/` or `rag-pipeline/`). Never edit `pyproject.toml` or `uv.lock` manually.

## Docker source configuration

All repository Dockerfiles use public Ubuntu and PyPI sources by default. The
root helper discovers every Dockerfile and switches its marked base-image,
Ubuntu apt, and Python package source blocks together:

```bash
bash configure-build-sources.sh revert  # BARC sources
bash configure-build-sources.sh apply   # public sources
```

## Port conventions

| Range | Subsystem | Typical services |
|-------|-----------|-----------------|
| 8080+ | `chatbot-service/` | Web app, file server, LLM inference, embedding APIs |
| 8090+ | `rag-pipeline/` | Text embed, multimodal embed, reranker, RAG API |
