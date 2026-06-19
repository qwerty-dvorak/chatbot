# CLAUDE.md — Monorepo Root

The chatbot-service can use a local GPU (`bash chatbot-service/run.sh local`),
a mock LLM (`bash chatbot-service/run.sh no-gemma`), or a RunPod cloud GPU
(`bash chatbot-service/run.sh runpod`). The RAG pipeline has similar modes
via `bash rag-pipeline/run.sh {local|runpod}`.

Tests hit the **real LLM endpoint** — no mocks or fakes. See `tests/run.sh`
for the unified test orchestrator.

## Structure

| Directory | Purpose |
|-----------|---------|
| `chatbot-service/` | Django chatbot with streaming LLM, RAG, memory, tool calling |
| `rag-pipeline/` | Standalone document ingestion + advanced RAG pipeline |
| `models/` | Model deployment scripts + .env configs (deploy-local.sh, deploy-runpod.sh, teardown-runpod.sh, clone_models.sh) |
| `db/` | PostgreSQL management scripts (start, stop, clear, exec, health) — port 5433 |
| `milvus/` | Milvus+minio+etcd management scripts (start, stop, clear, check) |
| `tests/` | Unified test orchestrator: tests/run.sh dispatches to tests/chatbot/, tests/rag/, tests/integration/ |
| `seed_data/` | Seed data for tests |
| `sample_data/` | Sample documents (text/, pdf/, image/) |

## Models

Model files live in a **separate git repo** at the same level as this repo (`../models/`).
They are mounted as Docker volumes at runtime — never copied into this repo.
See `chatbot-service/shell_scripts/start-services.sh` and `rag-pipeline/shell_scripts/start-services.sh` for mount paths.

LoRA adapters live in the separate sibling directory `../lora_adapters/`,
grouped by provider/owner and repository. Each repository contains
`adapter_config.json` and `adapter_model.safetensors`.

```
barc/
├── chatbot/          ← this repo
├── models/           ← separate git repo (HF model configs, tokenizers, safetensors)
│   ├── gemma-4-26B-A4B-it/
│   ├── llama-embed-nemotron-8b/
│   ├── nemotron-colembed-vl-8b-v2/
│   └── Qwen3-VL-Reranker-8B/
└── lora_adapters/
    └── EvilScript/
        ├── taboo-book-gemma-4-E4B-it/
        └── taboo-ship-gemma-4-E4B-it/
```

`models/deploy-runpod.sh` discovers this two-level layout, uses each local Git
`origin` (or derives `https://huggingface.co/<owner>/<repo>`), clones all
compatible adapters into `/lora_adapters/<owner>/<repo>` on the pod, and gives
vLLM those local paths. `models/deploy-local.sh` mounts the entire adapter root
read-only and registers adapters compatible with the local base model. Override
the root with `LORA_ADAPTERS_DIR=/absolute/path`.

To clone models locally:
```bash
bash models/clone_models.sh
```

## Architecture Overview

### RAG Pipeline (`rag-pipeline/`)

The RAG pipeline handles document ingestion and search as a standalone FastAPI
service (port 8093).  See `rag-pipeline/AGENTS.md` and `rag-pipeline/docs/architecture.md`
for full detail.

**Key design decisions:**

- **Hypothetical Questions are embedded as separate vectors** in Milvus at
  index time. Each chunk's generated questions become HYPOTHETICAL_QUESTION
  chunk objects, embedded into the same collection with `parent_id` → source
  chunk.  At search time, query-to-query resolution replaces question hits
  with their parent document chunks.  This is distinct from **HyDE** (query-time
  document generation).
- **Image ingestion is dual-track.** Each source image/PDF page is normalized,
  optionally split, persisted per derived image, and processed with
  `none`, basic Tesseract, or PaddleOCR. OCR text reuses the text pipeline;
  image vectors remain in the multimodal collection.
- **Single PostgreSQL** is shared between rag-pipeline (writes) and
  chatbot-service (reads). For two-server deployment, PostgreSQL listens on
  `*` with `pg_hba.conf` allowing remote connections.
- **Single Milvus** can be shared: rag-pipeline uses `rag_text_chunks` and
  `rag_image_chunks` collections; chatbot-service uses `user_memories`.

### Chatbot Service (`chatbot-service/`)

Django web app that provides the chat interface, user memory, and tool calling.
When `RAG_API_ENABLED=true`, it delegates all document ingest and search to the
RAG pipeline via HTTP (`RAG_API_BASE_URL`).

## Two-server deployment

```
┌── Server A: RAG Pipeline ──────────────────────────┐
│  rag-api (port 8093)    Milvus (:19530)              │
│  PostgreSQL (:5433)     RunPod / vLLM endpoints       │
│  PaddleOCR (local Python or RunPod :8000)             │
└──────────────────────────────────────────────────────┘
                │ HTTP :8093 │ TCP :5433 │ TCP :19530
┌──────────────────────────────────────────────────────┐
│  Server B: Chatbot Service                             │
│  chatbot-service (port 8080)  Chat LLM endpoint        │
│  RAG_API_BASE_URL=http://<server-a>:8093               │
│  POSTGRES_HOST=<server-a>                              │
└──────────────────────────────────────────────────────┘
```

PostgreSQL remote access:
```ini
postgresql.conf  →  listen_addresses = '*'
pg_hba.conf      →  host chatbot chatbot <client-ip>/32 md5
```

## Requirements

- Docker 24+ with NVIDIA Container Toolkit (for local GPU)
- 3x NVIDIA GPUs with 16GB+ VRAM each (one per model: text embed, multimodal embed, reranker)
  OR RunPod cloud GPUs (preferred for development)
- Python 3.12 (only needed for developing inside containers)

## Key principles

- **Models outside repo** — all model files live in a sibling `../models/` git repo;
  cloned once via `models/clone_models.sh`. Docker volumes mount them at runtime.
- **LoRAs outside repo** — adapters live under
  `../lora_adapters/<provider>/<repository>/`. Their base-model metadata must
  match the active chat model; incompatible adapters are skipped.
- **No Docker Compose** — services are started with plain `docker run` commands or
  native processes.
- **Python images** always use `FROM ubuntu:24.04` as base; dependencies managed with `uv`.
- **Reproducible uv resolution** — every `pyproject.toml` contains `[tool.uv]` with
  `exclude-newer = "2025-10-23T12:36:00Z"`.
- **Milvus** is the vector store, started via `milvus/start.sh`.
- **PostgreSQL 16** runs as a standalone container shared between services, started via `db/start.sh`.
- **Adding Python dependencies** — always use `uv add <package>` inside the relevant
  subdirectory (`chatbot-service/` or `rag-pipeline/`). Never edit `pyproject.toml`
  or `uv.lock` manually.

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
