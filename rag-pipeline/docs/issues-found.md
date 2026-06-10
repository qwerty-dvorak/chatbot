# Issues Found, Fixed & Analysis

Bugs, design issues, and architectural analysis discovered during development.

---

## Fixed

### 1. `embed.py` — Double `openai/` prefix in text embedding model name

**File:** `pipeline/embed.py:98`

**Before:**
```python
response = litellm.embedding(
    model=f"openai/{cfg.text_embedding_model}",  # BUG: "openai/openai/mock-text-embed"
    ...
)
```

**After:**
```python
response = litellm.embedding(
    model=cfg.text_embedding_model,  # value already has "openai/" prefix in .env
    ...
)
```

**Impact:** With the default `.env.example` value (`openai/mock-text-embed`), litellm
received `openai/openai/mock-text-embed`. The mock server ignored the model name so
dev worked; a real vLLM endpoint would reject or silently misroute it.

---

### 2. `embed.py` — Broken JPEG magic-byte detection

**File:** `pipeline/embed.py:_image_to_data_url`

**Before:**
```python
elif image_bytes[:4] in (b"\x00\x00\x00\x0c", b"\xff\x4f\xff\x51"):
    mime = "image/jp2"
```

These are JPEG2000 signatures, not standard JPEG. Standard JPEG starts with `\xff\xd8`.
Normal JPEG files fell through to the default `image/jpeg` — which was coincidentally
correct — but JPEG2000 files would be misidentified.

**After:**
```python
elif image_bytes[:2] == b"\xff\xd8":
    mime = "image/jpeg"
else:
    mime = "image/jpeg"  # JPEG2000 and other formats sent as JPEG
```

---

### 3. `chunk.py` — Image chunks got `ChunkType.TEXT`

**File:** `pipeline/chunk.py:_image_chunks` and `pipeline/ingest.py`

Image chunks created for PDF pages were tagged `ChunkType.TEXT` instead of the
new `ChunkType.IMAGE`. This made it impossible to distinguish image chunks from
text chunks by type alone — callers had to check `chunk.image_data is not None`.

**Fix:** Added `ChunkType.IMAGE = "image"` to the `ChunkType` enum in `models.py`,
and updated `chunk.py` and `ingest.py` to use it.

---

### 4. `retrieve.py` — BM25 search crashed on fresh deployment

**File:** `pipeline/retrieve.py:bm25_search`

On a fresh deployment with no documents ingested, `load_bm25_index()` raised
`FileNotFoundError`. This propagated through `hybrid_search` and crashed any search
request before the first ingest was complete.

**Fix:** Catch `FileNotFoundError` in `bm25_search` and return an empty list.

```python
try:
    bm25, chunks = load_bm25_index()
except FileNotFoundError:
    return []
```

---

### 5. `retrieve.py` — `HYBRID_ALPHA` was read from config but never applied

**File:** `pipeline/retrieve.py:hybrid_search` and `_rrf_fusion`

`cfg.hybrid_alpha` was documented as "0=pure BM25, 1=pure vector" but `_rrf_fusion`
always gave equal weight to both lists, ignoring the setting.

**Fix:** Added `weights` parameter to `_rrf_fusion` and pass `[cfg.hybrid_alpha, 1-cfg.hybrid_alpha]`
from `hybrid_search`.

---

### 6. `ingest.py` — Registry key collision on same filename in different directories

**File:** `pipeline/ingest.py`

**Before:**
```python
registry_key = file_path.name  # "report.pdf" — collides with any other "report.pdf"
```

**After:**
```python
registry_key = str(file_path.resolve())  # "/abs/path/to/report.pdf"
```

**Impact:** Ingesting two files with the same name from different directories would
skip the second one (seen as duplicate).

---

### 7. `api.py` — Non-thread-safe mutation of global `cfg.query_enhancements`

**File:** `api.py:search_endpoint`

**Before:**
```python
cfg.query_enhancements = body.enhancements  # mutates shared global state
```

Under concurrent requests, request A's override could bleed into request B's search.

**Fix:** Pass `enhancements` as an explicit parameter to `search()` instead of mutating
the global config. `search()` and `enhance_query()` now accept `enhancements: list[str] | None`.

---

### BM25 index - concurrent writers and partial reads

**Fix:** Ingestion now runs through one durable worker, so BM25 mutations are
serialized. Persistence writes a temporary pickle, calls `fsync`, and atomically
replaces the active index so concurrent search readers cannot observe a partial file.

---

### Promotion left stale BM25 chunks

**Fix:** Promotion now removes matching source chunks from BM25 before rebuilding
and indexing the promoted representation.

---

## Remaining Known Issues

### Instant-tier image files — no text content

When ingesting standalone images (`.png`, `.jpg`) at the instant tier, OCR is skipped
and multimodal embedding is disabled. The resulting chunk has empty text and is not
embedded at all.

**Workaround:** Use slow or global tier for image-only documents that need OCR. The
instant tier is designed for text-rich files (PDFs with text layers, `.md`, `.txt`).

---

### Milvus scalar field filter — performance on large collections

`delete_chunks_by_source` (used by `promote_document`) queries all chunks matching
`source_path` via a scalar filter. Without a scalar index on `source_path`, this is
a full collection scan. On collections with millions of chunks, this will be slow.

**Workaround for production:** Add a scalar index on `source_path` in `_ensure_collection`:
```python
schema.add_field("source_path", DataType.VARCHAR, max_length=512)
# After create_collection:
client.create_index(collection_name=name, field_name="source_path", index_params={...})
```

---

### Hypothetical-question augmentation — sequential LLM calls

At global-tier ingestion, hypothetical questions are generated per chunk sequentially.
For a 100-chunk document with 3 questions per chunk, this is 300 serial LLM calls.

**Workaround:** Increase `cfg.chunk_size` to reduce chunk count, or add a
`ThreadPoolExecutor` around `hypothetical_questions_for_chunk` in `tiers.py`.

---

## Architectural Analysis

### Primary use case: long-running global knowledge base

The most common deployment is a continuous global knowledge base where documents
are ingested in the background and searched immediately. This means:

1. **Ingestion timing per PDF is not critical** — embedding setup latency (first
   call to a model endpoint) is amortized over many documents. The pipeline's
   single-worker design is appropriate for this workload.

2. **Search must be available during ingestion** — the BM25 atomic-replace pattern
   and Milvus's ability to serve reads during writes ensure this. Verified by
   integration tests.

3. **Job durability is essential** — the SQLite queue survives API restarts. Jobs
   left in `running` state are requeued at startup. This is at-least-once execution,
   which is acceptable for idempotent ingestion.

### Chatbot-service integration pattern

The RAG pipeline API (`rag-pipeline/api.py`) exposes all ingestion and search
endpoints. The chatbot-service integration (`chatbot-service/apps/knowledge/rag_client.py`)
calls these endpoints for document upload and search. Key design decisions:

1. **Visibility maps to tier**: `private` → `instant`, `shared` → `slow`, `global` → `global`
2. **RAG API is optional**: when `RAG_API_ENABLED=false`, the chatbot-service falls
   back to its local ingestion pipeline
3. **Global knowledge base**: documents with `visibility=global` are processed at
   the global tier for maximum quality

### Mock server architecture

Two separate mock servers provide a complete test environment:

| Server | Location | Purpose |
|--------|----------|---------|
| Mock AI model server | `mock_server/server.py` | Replaces vLLM pods (chat, embed, reranker, OCR) |
| Mock RAG API | `mock_rag/server.py` | Replaces the RAG pipeline API endpoints |

Both are stdlib-only (no dependencies) and run in separate Docker containers
with `--network host` for development testing.

### Shell script simplification

The three Milvus startup scripts (`start-services.sh`, `start_milvus.sh`,
`runpod_start_local_milvus.sh`) share the same etcd+minio+milvus pattern. The
lightest one (`runpod_start_local_milvus.sh`) is the canonical local Milvus
startup; it uses ephemeral `/tmp` storage and a minimal network.

The `start-services.sh` script remains for the full production stack (vLLM + RAG API).
For pure API development, the mock servers plus local Milvus provide a faster
feedback loop.
