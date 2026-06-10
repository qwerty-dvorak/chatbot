# RAG Pipeline Architecture

## Goals

The service provides local document ingestion and retrieval without cloud
APIs. Long-running ingestion is separated from request handling so uploads can
return immediately, survive API restarts, and avoid concurrent BM25 writers.

Primary constraints:

- Plain `docker run`; no Docker Compose.
- Models and Milvus are local services.
- One API process and one ingestion worker per deployment.
- Durable uploads, job state, registry, and BM25 data under `data/`.
- Search remains available while ingestion jobs run.

## Runtime Topology

```text
Client
  |
  | HTTP :8093
  v
+----------------------------- rag-api container -----------------------------+
|                                                                             |
|  FastAPI request layer                                                      |
|    POST /v1/ingest ------ persist files ------+                             |
|    POST /v1/promote --------------------------+                             |
|    GET  /v1/ingestions/{id}                  |                             |
|    POST /v1/search ----------------------+    |                             |
|                                           |    v                             |
|                                           |  SQLite jobs.sqlite3             |
|                                           |    queued/running/result/error   |
|                                           |                                  |
|                                           |  single worker thread            |
|                                           |    claim oldest queued job       |
|                                           |    extract -> OCR -> chunk       |
|                                           |    augment -> embed -> index     |
|                                           |                                  |
|                                           +-------------------------+        |
+--------------------------------------------------------------------|--------+
                                                                     |
                 +--------------------+--------------------+----------+
                 |                    |                    |
                 v                    v                    v
          model endpoints          Milvus           local data volume
          chat/embed/OCR/           :19530           uploads/
          reranker                                   jobs.sqlite3
                                                     registry JSON
                                                     BM25 pickle
```

The Docker image runs Uvicorn with `--workers 1`. Multiple Uvicorn processes
would each start a worker thread and would weaken the intentional single-writer
model. Horizontal API scaling requires extracting the worker into a separate
process and adding a cross-process lease.

## Request And Job Lifecycle

### Upload

1. FastAPI validates multipart fields.
2. Each filename is reduced to its basename and de-duplicated within the
   request.
3. Files stream to
   `INGESTION_DATA_DIR/uploads/<job-id>/`; the request does not buffer the
   entire file in memory.
4. A `queued` row is committed to SQLite only after all files are durable.
5. The worker is notified.
6. The API returns `202 Accepted` and a job status URL.

If file persistence or job creation fails, the incomplete upload directory is
removed and no runnable job remains.

### Claim And Execution

1. The worker opens `BEGIN IMMEDIATE`.
2. It selects the oldest `queued` row.
3. It updates that row to `running` and commits.
4. It executes ingestion or promotion outside the SQLite transaction.
5. It writes either `succeeded` plus JSON result or `failed` plus traceback.

The worker handles one job at a time. This serializes:

- ingestion registry updates;
- BM25 read/rebuild/replace operations;
- document promotion deletes followed by replacement indexing.

Milvus can serve searches while inserts occur. BM25 persistence uses a
temporary file plus `os.replace`, so search readers observe either the old
complete index or the new complete index.

### Restart Recovery

SQLite and uploaded files are persisted on the data volume. During API startup:

1. schema creation runs idempotently;
2. jobs left in `running` state are moved back to `queued`;
3. the worker starts and resumes oldest-first processing.

This is at-least-once execution. A process can stop after a remote Milvus insert
but before marking the job successful. Re-execution can therefore repeat work.
Chunk IDs are generated per run, so strict exactly-once behavior would require
stable document/chunk identifiers and transactional indexing across Milvus and
the local job database.

## Job Storage

SQLite table `ingestion_jobs`:

| Column | Purpose |
|--------|---------|
| `id` | UUID-like hexadecimal job identifier |
| `kind` | `ingest` or `promote` |
| `status` | Queue lifecycle state |
| `payload_json` | Internal execution parameters and persisted path |
| `filenames_json` | User-facing uploaded filenames |
| `result_json` | Pipeline statistics and source paths |
| `error` | Background exception traceback |
| `created_at` | UTC ISO-8601 creation time |
| `started_at` | Most recent claim time |
| `completed_at` | Terminal transition time |

SQLite uses WAL mode and a busy timeout. API reads, cancellation, and the
worker claim operation can safely use separate connections.

## Ingestion Pipeline

```text
persisted file
  |
  v
extract.py
  |-- text/Markdown/RST: decoded text
  |-- PDF: pypdf text layer plus extractable embedded images
  `-- image: raw bytes
  |
  +-- slow/global image bytes --> ocr.py --> OCR text
  |
  v
RawDocument
  |
  v
chunk.py
  |-- recursive
  |-- sentence_window
  `-- hierarchical
  |
  +-- optional hypothetical questions via query.py
  |
  +-- text chunks  --> embed.py /v1/embeddings
  `-- image chunks --> embed.py /pooling
  |
  v
index.py
  |-- rag_text_chunks in Milvus
  |-- rag_image_chunks in Milvus
  `-- atomic BM25 pickle replacement
```

PDF handling is intentionally described narrowly: the current extractor uses
the existing PDF text layer and images exposed by `pypdf`. It does not render
every PDF page at a configured DPI. `ocr_dpi` remains a tier policy value for a
future page-rendering extractor but does not currently change pypdf output.

Per-file failures are collected in the job result:

```json
{
  "files_processed": 2,
  "files_failed": 1,
  "errors": [
    {"file": "/app/data/.../broken.pdf", "error": "invalid PDF header"}
  ]
}
```

A job may therefore finish as `succeeded` with partial file errors. If every
file fails, the worker marks the job `failed`.

## Tier Policies

`pipeline/tiers.py` maps each `IngestionTier` to immutable options.

| Policy | Instant | Slow | Global |
|--------|---------|------|--------|
| Text extraction | yes | yes | yes |
| OCR extracted images | no | yes | yes |
| Text embedding | yes | yes | yes |
| Image embedding | no | yes | yes |
| Chunk strategy | recursive | sentence window | hierarchical |
| Hypothetical questions | 0 | 2/chunk | 3/chunk |
| Query enhancements | none | HyDE | HyDE, sub-query, stepback |
| Reranker | off | on | on |

Request form fields can override chunk strategy and hypothetical-question
generation without mutating global configuration.

## Promotion

Promotion is represented as a queue job, not an inline API operation:

1. verify the persisted source exists;
2. optionally delete source rows from both Milvus collections;
3. remove matching source chunks from BM25 and atomically rebuild it;
4. remove the source from the ingestion registry;
5. ingest with the target tier;
6. record deleted counts and new ingestion statistics.

Deletion and replacement are not one transaction across Milvus and BM25. A
promotion failure can temporarily leave a document absent or partially
reindexed. A future version can use versioned document IDs and switch an active
version only after replacement indexing completes.

## Search Pipeline

```text
request query
  |
  +-- explicit enhancements, or tier defaults
  v
query.py: raw / HyDE / sub-queries / stepback
  |
  +-- text embedding
  |
  +-- vector search in Milvus
  +-- lexical search in BM25
  `-- hybrid weighted reciprocal-rank fusion
  |
  v
deduplicate by chunk ID
  |
  +-- optional reranker /score
  |
  +-- best-effort parent context fetch
  v
SearchResponse
```

Enhancement selection is passed as a function argument. Request handlers do not
mutate the global `cfg.query_enhancements`, avoiding cross-request leakage.

## Milvus Collections

Text and image embeddings use separate collections because their dimensions
can differ.

| Field | Type | Notes |
|-------|------|-------|
| `id` | VARCHAR(64) | Chunk primary key |
| `source_path` | VARCHAR(512) | Persisted upload path |
| `text` | VARCHAR(65535) | Extracted, OCR, or generated text |
| `chunk_type` | VARCHAR(32) | text/image/parent/child/window/summary |
| `parent_id` | VARCHAR(64) | Parent context reference |
| `window_text` | VARCHAR(65535) | Sentence window or fetched parent |
| `metadata_json` | VARCHAR(4096) | Tier and source metadata |
| `embedding` | FLOAT_VECTOR | Configured model dimension |

The vector index is `IVF_FLAT` with inner-product distance.

## Module Ownership

| Module | Responsibility |
|--------|----------------|
| `api.py` | HTTP validation, upload persistence, job resources, search routes |
| `pipeline/jobs.py` | SQLite repository and worker lifecycle |
| `pipeline/tiers.py` | Tier policy, ingestion orchestration, promotion |
| `pipeline/extract.py` | File-type extraction and instant-tier text-only PDF path |
| `pipeline/ocr.py` | Concurrent calls to the local OCR endpoint |
| `pipeline/chunk.py` | Chunking strategies |
| `pipeline/embed.py` | Text and multimodal model adapters |
| `pipeline/index.py` | Milvus schema/writes and atomic BM25 persistence |
| `pipeline/query.py` | Query and index-time LLM enhancements |
| `pipeline/retrieve.py` | Vector, BM25, hybrid fusion, reranking |
| `pipeline/search.py` | End-to-end synchronous retrieval |
| `mock_server/server.py` | Deterministic local model API substitutes |

## Deployment And Persistence

The RAG API must mount `/app/data`:

```bash
docker run -d \
  --name rag-api \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -p 8093:8093 \
  rag-api
```

Recommended container values:

```text
BM25_INDEX_PATH=/app/data/bm25_index.pkl
INGESTION_DATA_DIR=/app/data/ingestion
```

Do not run multiple `rag-api` containers against the same queue directory. The
SQLite claim is safe, but each container also owns a worker and can execute
different ingestion jobs concurrently, reintroducing shared-index races.

## Mock Test Topology

Two mock servers provide a complete offline test environment:

### Mock AI Model Server (`mock_server/server.py`)

Starts deterministic local substitutes for the 5 RunPod vLLM pods:

| Port | Route | RunPod Equivalent |
|------|-------|-------------------|
| 9000 | `/v1/chat/completions` | Query and hypothetical-question LLM |
| 9001 | `/v1/embeddings` | Text embeddings (nvidia/llama-embed-nemotron-8b) |
| 9002 | `/pooling` | Multimodal embeddings (ColBERT-style pooling) |
| 9003 | `/score` | Reranker (Qwen/Qwen3-VL-Reranker-2B) |
| 9004 | `/v1/chat/completions` | OCR (PaddleOCR-VL) |

All five servers run in a single Docker container using stdlib-only threading,
matching the exact request/response shapes from `docs/runpod_api.md`.

### Mock RAG API Server (`mock_rag/server.py`)

Stdlib-only mock of the full RAG Pipeline API (`docs/api.md`):

| Port | Route | Description |
|------|-------|-------------|
| 8093 | all RAG endpoints | Health, ingest, search, promote, collections |

The mock RAG API simulates async ingestion with background threads, making it
suitable for testing the full upload→poll→search flow without Milvus or any
model endpoints.

### Integration test

`mock_server/test_integration.sh` starts both mock servers plus local Milvus;
queues a text fixture; polls the job to completion; then verifies job listing,
collections, hybrid search, vector search, and BM25 search.

`start_mock_all.sh` starts all three services with a single command.
`start_mock_all.sh --clean` tears everything down.
