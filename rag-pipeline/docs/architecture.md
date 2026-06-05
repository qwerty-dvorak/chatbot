# RAG Pipeline — Architecture

## Overview

The RAG pipeline is a standalone FastAPI service that provides document ingestion and semantic search. It is designed to run fully locally: all LLM, embedding, reranker, and OCR calls go to local vLLM endpoints.

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI (port 8093)                       │
│  POST /v1/ingest  │  POST /v1/search  │  POST /v1/promote       │
└──────────┬──────────────────┬───────────────────┬───────────────┘
           │                  │                   │
    ┌──────▼──────┐    ┌──────▼──────┐    ┌──────▼──────┐
    │  pipeline/  │    │  pipeline/  │    │  pipeline/  │
    │  tiers.py   │    │  search.py  │    │  tiers.py   │
    │  ingest.py  │    │  query.py   │    │ (promote)   │
    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
           │                  │                   │
    ┌──────▼──────────────────▼───────────────────▼──────┐
    │                  Pipeline Modules                    │
    │  extract → ocr → chunk → embed → index → retrieve  │
    └──────────────────────────┬──────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
    ┌───────────┐      ┌───────────────┐    ┌──────────────┐
    │  Milvus   │      │ BM25 (pickle) │    │  vLLM pods   │
    │  :19530   │      │ bm25_index.   │    │ embed/rerank │
    │ text+img  │      │    pkl        │    │ /ocr/chat    │
    └───────────┘      └───────────────┘    └──────────────┘
```

## Module Map

| Module | Role |
|--------|------|
| `pipeline/config.py` | Global `Config` dataclass loaded from `.env` |
| `pipeline/models.py` | Data classes: `RawDocument`, `Chunk`, `EmbeddedChunk`, `SearchResult`; enums: `IngestionTier`, `ChunkType`, `ContentType` |
| `pipeline/extract.py` | File content extraction (text, PDF, image).  `extract()` renders PDFs as PNG; `extract_fast()` uses PyMuPDF text layer |
| `pipeline/ocr.py` | PaddleOCR-VL-1.6 via vLLM — converts page images to text |
| `pipeline/chunk.py` | Three chunking strategies: `recursive`, `sentence_window`, `hierarchical` |
| `pipeline/embed.py` | Text embedding (`/v1/embeddings`) and multimodal embedding (`/pooling`) |
| `pipeline/index.py` | Milvus collection management, BM25 index persistence |
| `pipeline/retrieve.py` | Vector search, BM25 search, RRF hybrid fusion, reranker |
| `pipeline/query.py` | Query enhancements: HyDE, sub-queries, stepback, hypothetical questions |
| `pipeline/search.py` | End-to-end search orchestrator |
| `pipeline/tiers.py` | Three-tier ingestion system + tier promotion |
| `pipeline/ingest.py` | Legacy ingestion entry point (wraps tier system) |
| `api.py` | FastAPI application |
| `mock_server/server.py` | Five-port stdlib mock server for local dev/testing |

## Data Flow

### Ingestion

```
File
  │
  ▼ extract.py / extract_fast.py
RawDocument {path, content_type, text, images[(bytes, "")], metadata}
  │
  ├─ (PDFs, slow/global tier) ──► ocr.py ──► page texts
  │
  ▼ chunk.py
list[Chunk] {id, source_path, text, chunk_type, metadata, image_data?}
  │
  ├─ text chunks ──► embed.py (embed_text) ──► EmbeddedChunk
  └─ image chunks ─► embed.py (embed_multimodal) ──► EmbeddedChunk
  │
  ▼ index.py
Milvus (text_collection / image_collection) + BM25 pickle
```

### Search

```
query string
  │
  ▼ query.py (enhance_query)
[query, hyde_doc?, sub_q1?, sub_q2?, stepback?]
  │
  ├─ each enhanced query ──► embed.py ──► embedding
  │                    └───► retrieve.py (vector/BM25/hybrid)
  │
  ▼ RRF fusion → deduplicate
  │
  ▼ rerank (optional)
  │
  ▼ parent-context fetch (CHILD chunks)
  │
list[SearchResult]
```

## External Service Endpoints

All services are started via `start-services.sh`.

| Service | Port | Endpoint | Model |
|---------|------|----------|-------|
| rag-text-embed | 8001 | `/v1/embeddings` | nvidia/llama-embed-nemotron-8b |
| rag-multimodal-embed | 8002 | `/pooling` | nvidia/nemotron-colembed-vl-8b-v2 |
| rag-reranker | 8003 | `/score` | Qwen3-VL-Reranker-2B |
| rag-ocr | 8004 | `/v1/chat/completions` | PaddlePaddle/PaddleOCR-VL-1.6 |
| rag-api | 8093 | `/v1/ingest`, `/v1/search` | — |
| milvus-standalone | 19530 | gRPC | — |

In development, `mock_server/server.py` provides all five endpoints on ports 9000–9004 with deterministic fake responses.

## Milvus Schema

Both `rag_text_chunks` and `rag_image_chunks` share the same schema:

| Field | Type | Notes |
|-------|------|-------|
| `id` | VARCHAR(64) | Primary key — UUID4 hex |
| `source_path` | VARCHAR(512) | Absolute path of the source file |
| `text` | VARCHAR(65535) | Chunk text (OCR text for image chunks) |
| `chunk_type` | VARCHAR(32) | `text`, `image`, `parent`, `child`, `sentence_window`, `summary` |
| `parent_id` | VARCHAR(64) | Set for `child` and `sentence_window` chunks |
| `window_text` | VARCHAR(65535) | Surrounding context (sentence_window strategy) |
| `metadata_json` | VARCHAR(4096) | JSON blob: `ingestion_tier`, `image_index`, etc. |
| `embedding` | FLOAT_VECTOR(dim) | 768-dim for text, 512-dim for image |

Index: `IVF_FLAT` with `IP` (inner product) metric on the `embedding` field.

## BM25 Index

Stored as a pickle file at `cfg.bm25_index_path` (`./bm25_index.pkl` by default).

The pickle contains `{"bm25": BM25Okapi, "chunks": list[Chunk]}`.  Chunks are added
incrementally; the index is rebuilt from scratch each time new documents are ingested.

> **Note:** The BM25 index is not thread-safe for concurrent writes.  Concurrent ingest
> calls may cause the second writer to overwrite the first's additions.  A file lock or
> database-backed BM25 implementation is recommended for production multi-threaded use.

## Key Design Decisions

- **No Docker Compose** — services started with plain `docker run` (see `start-services.sh`).
- **No external model downloads** — all models must be present locally before starting services.
- **Two Milvus collections** — text (768-dim) and image (512-dim) are separate because the models have different output dimensions.
- **Multimodal model is T/I, not T+I** — Qwen3VLNemotronEmbed supports text-only OR image-only inputs per request, never combined.  Image chunks use the image path; text chunks use the text path.
- **RRF fusion** — weighted Reciprocal Rank Fusion (controlled by `HYBRID_ALPHA`) merges vector and BM25 results.  This avoids score-space normalization problems.
- **Reranker on original query** — the reranker always scores against the original query, not the HyDE/enhanced variants, to avoid semantic drift.
