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

## Deployment Topologies

### Single-server (dev / small-scale)

All services run on one machine, typically inside Docker containers on a shared
network (`rag_net`):

```text
┌─────────────── host machine ───────────────────────────────────────┐
│                                                                     │
│  rag-api (port 8093) ──┐                                           │
│                        │                                           │
│  RunPod / vLLM   ◄─────┤  HTTP calls for embed/rerank/chat/OCR     │
│  endpoints       ◄─────┤                                           │
│                        │                                           │
│  Milvus (:19530) ◄────┤  vector reads/writes                       │
│                        │                                           │
│  PostgreSQL (:5432) ◄─┤  document + chunk CRUD                     │
│  (rag-postgres)        │                                           │
│                        │                                           │
│  chatbot-service       │  (optional — can run on same host)        │
│  (port 8080)  ◄────────┘  connects to same PostgreSQL + Milvus     │
└─────────────────────────────────────────────────────────────────────┘
```

### Two-server (production)

The RAG pipeline and chatbot-service run on separate machines, sharing
PostgreSQL and (optionally) Milvus. The chatbot-service calls the RAG API for
ingest + search, avoiding the need to embed documents on the chat server.

```text
┌── Server A: RAG Pipeline ───────────────────────────────────────┐
│  rag-api (port 8093)       ←── RAG_API_BASE_URL                   │
│                                                                   │
│  Milvus (:19530)           ── vector store                         │
│  PostgreSQL (:5432)        ── shared tables                        │
│                                                                   │
│  RunPod / vLLM endpoints   ── embed/rerank/chat/OCR               │
└───────────────────────────┬────────────────────────────────────────┘
                            │ HTTP :8093 (search/ingest)
                            │ TCP :5432  (PostgreSQL)
                            │ TCP :19530 (Milvus — optional)
┌───────────────────────────┴────────────────────────────────────────┐
│  Server B: Chatbot Service                                          │
│  chatbot-service (port 8080)  Django web + worker                   │
│  RAG_API_BASE_URL=http://<server-a>:8093                            │
│  POSTGRES_HOST=<server-a>     (same PostgreSQL)                     │
│  MILVUS_HOST=<server-a>       (same Milvus, for user memories)      │
│  Chat LLM endpoint            (RunPod or other)                     │
└─────────────────────────────────────────────────────────────────────┘
```

**PostgreSQL remote access setup:**

On the PostgreSQL server, two configuration changes are required so the
chatbot-service can connect over TCP:

```ini
postgresql.conf  →  listen_addresses = '*'
```

```text
pg_hba.conf  →  host chatbot chatbot <client-ip>/32 md5
```

**Milvus sharing:**

Both services can share one Milvus instance. The chatbot-service stores user
memory vectors in the `user_memories` collection; the rag-pipeline stores
document chunks in `rag_text_chunks` and `rag_image_chunks`. Ensure the
Milvus server's `--bind-address` listens on `0.0.0.0` so the remote
chatbot-service can connect.

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
|                                           |    extract → OCR → chunk        |
|                                           |    → hyde → embed → index       |
|                                           |    (see Ingestion Pipeline)      |
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
3. Files stream to `INGESTION_DATA_DIR/uploads/<job-id>/`; the request does
   not buffer the entire file in memory.
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
  +-- slow/global image bytes → ocr.py → OCR text
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
  +-- hypothetical questions via query.py
  |   For each chunk, LLM generates N questions.  Each question becomes a
  |   Chunk with chunk_type=HYPOTHETICAL_QUESTION and parent_id pointing
  |   to the source chunk.  These question chunks are embedded and indexed
  |   into Milvus as separate vectors alongside the document chunks.
  |
  +-- text chunks  → embed.py /v1/embeddings
  `-- image chunks → embed.py /pooling
  |
  v
index.py
  |-- rag_text_chunks in Milvus
  |   Contains both document chunks (text/parent/child/sentence_window)
  |   and hypothetical question vectors (HYPOTHETICAL_QUESTION type with
  |   parent_id → source chunk).
  |-- rag_image_chunks in Milvus
  `-- atomic BM25 pickle replacement
```

### Step Detail — Hypothetical Question Vector Indexing

1. For each text chunk, the LLM generates N hypothetical questions that the
   chunk would answer (N = `hypothetical_questions_per_chunk`, configured per
   tier: 0 for instant, 2 for slow, 3 for global).
2. Each question is placed into its own `Chunk` object with:
   - `chunk_type = ChunkType.HYPOTHETICAL_QUESTION`
   - `parent_id = <source chunk's UUID>`
   - `text = <the question text>`
   - `source_path = <same as parent>`
3. These question chunks are embedded using the same text embedding model as
   the document chunks.
4. They are indexed into the same Milvus collection (`rag_text_chunks`) as
   separate rows with `chunk_type='hypothetical_question'` and the
   `parent_id` field pointing to the source document chunk.
5. The original `hyde_questions` field in the PostgreSQL chunk metadata JSONB
   is still populated for reproducibility, but it is the **Milvus vectors**
   that power query-to-query search.

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
| Hypothetical questions per chunk | 0 | 2 | 3 |
| Query enhancements | none | HyDE | HyDE, sub-query, stepback |
| Reranker | off | on | on |

Request form fields can override chunk strategy and hypothetical-question
generation without mutating global configuration.

## Promotion

Promotion is represented as a queue job, not an inline API operation:

1. verify the persisted source exists;
2. optionally delete source rows from both Milvus collections (including
   hypothetical question vectors with matching `parent_id`);
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
  +-- text embedding (each enhanced query)
  |
  +-- vector search in Milvus → returns both document chunks AND
  |   hypothetical question chunks (if query matches a question vector)
  |
  +-- lexical search in BM25
  |
  +-- hybrid weighted reciprocal-rank fusion
  |
  v
  +-- QUERY-TO-QUERY RESOLUTION:
  |   For each result whose chunk_type is HYPOTHETICAL_QUESTION:
  |     1. Query Milvus by parent_id to find the source document chunk.
  |     2. Replace the question chunk with the parent chunk in the result.
  |     3. Mark retrieval_method as "query_to_query".
  |     4. If the same parent chunk was also found directly, dedup
  |        keeps the highest score.
  |
  v
deduplicate by chunk ID
  |
  +-- optional reranker /score (uses original query, not enhanced)
  |
  +-- best-effort parent context fetch (for CHILD chunks)
  v
SearchResponse
```

### Query Enhancement Quick Reference

| Technique | When | What it does | Implemented in |
|-----------|------|-------------|----------------|
| HyDE | Query-time | LLM writes a hypothetical *document* from user query; embed that doc → search against chunk vectors | `query.py:hyde()` |
| Sub-queries | Query-time | Decompose complex query into 2-4 simpler sub-questions | `query.py:sub_queries()` |
| Stepback | Query-time | Broader reformulation for recall | `query.py:stepback()` |
| Hypothetical Questions | **Index-time** | LLM generates questions per chunk → each question embedded as separate vector in Milvus → query-to-query search resolves back to source chunks | `query.py:hypothetical_questions_for_chunk()` + `text_pipeline.py` embed/index + `search.py` resolution |

The key distinction: **HyDE** is a query-time embedding-space trick (replace
query → search doc vectors). **Hypothetical Questions** is an index-time
data-augmentation strategy (pre-compute question vectors → search questions →
resolve to docs). Both can be active simultaneously and complement each other.

Enhancement selection is passed as a function argument. Request handlers do not
mutate the global `cfg.query_enhancements`, avoiding cross-request leakage.

## Milvus Collections

Text and image embeddings use separate collections because their dimensions
can differ.

### rag_text_chunks

| Field | Type | Notes |
|-------|------|-------|
| `id` | VARCHAR(64) | Chunk primary key (UUID hex) |
| `source_path` | VARCHAR(512) | Persisted upload path inside container |
| `text` | VARCHAR(65535) | Extracted, OCR, or **generated question text** |
| `chunk_type` | VARCHAR(32) | `text`/`image`/`parent`/`child`/`sentence_window`/`summary`/ **`hypothetical_question`** |
| `parent_id` | VARCHAR(64) | Parent chunk UUID (for CHILD/SENTENCE_WINDOW/HYPOTHETICAL_QUESTION) |
| `window_text` | VARCHAR(65535) | Sentence window or fetched parent context |
| `metadata_json` | VARCHAR(4096) | Tier and source metadata |
| `embedding` | FLOAT_VECTOR(4096) | Embedding vector using inner-product distance |

The vector index is `IVF_FLAT` with inner-product distance.

### rag_image_chunks

Same schema but `embedding` uses `MULTIMODAL_EMBEDDING_DIM`.

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
| `pipeline/query.py` | Query-time enhancements (HyDE/sub-queries/stepback) AND index-time hypothetical question generation |
| `pipeline/retrieve.py` | Vector, BM25, hybrid fusion, reranking |
| `pipeline/search.py` | End-to-end synchronous retrieval including query-to-query resolution |
| `pipeline/text_pipeline.py` | Document ingest orchestration: chunk → hyde → persist → embed → index (including hypothetical question chunks) |
| `pipeline/image_pipeline.py` | Image-only document ingest orchestration |
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

### Two-server deployment (rag-api + chatbot-service on separate machines)

```bash
# Server A (RAG pipeline) — start Milvus + PostgreSQL + rag-api
docker run -d --name test-milvus \
  --network rag_net \
  milvusdb/milvus:latest

docker run -d --name rag-postgres \
  --network rag_net \
  -e POSTGRES_DB=chatbot \
  -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot \
  -p 5432:5432 \
  postgres:16

docker run -d --name rag-api \
  --network rag_net \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -p 8093:8093 \
  rag-api

# Server B (Chatbot) — connect to remote PostgreSQL + rag-api
docker run -d --name chatbot-web \
  -e POSTGRES_HOST=<server-a-ip> \
  -e RAG_API_ENABLED=true \
  -e RAG_API_BASE_URL=http://<server-a-ip>:8093 \
  -p 8080:8080 \
  chatbot
```

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

## Hierarchical Index (Coarse-to-Fine Retrieval)

When processing vast document spaces, a flat vector search across millions of
granular chunks can introduce noise and latency. The hierarchical index
mitigates this with a two-stage coarse-to-fine retrieval strategy.

### Architecture

```text
                    ┌──────────────┐
                    │    Query     │
                    └──────┬───────┘
                           │ ② embed + search
                           ▼
              ┌────────────────────────┐
              │ Index of Summary       │
              │ Vectors (doc-level)    │
              │ ┌──────────────────┐   │
              │ │ [matched]        │   │  ③ isolate
              │ └──────────────────┘   │     relevant
              │ ┌──────────────────┐   │     doc
              │ │                  │   │
              │ └──────────────────┘   │
              └─────────┬──────────────┘
                        │ ④ narrow
                        ▼
              ┌────────────────────────┐
              │ Vector Store: Chunks   │
              │ (filtered by matched   │
              │  doc source_path)      │
              │ ┌─[hit1]───────────┐   │
              │ └──────────────────┘   │
              │ ┌─[hit2]───────────┐   │  ⑤ high-res
              │ └──────────────────┘   │     lookup
              └─────────┬──────────────┘
                        │ ⑥ top-k
                        ▼
              ┌──────────────────┐
              │ Top K Chunks     │
              └────────┬─────────┘
                       │ ⑦ context
                       ▼
              ┌──────────────────┐
              │       LLM        │
              └────────┬─────────┘
                       │ ⑧ answer
                       ▼
              ┌──────────────────┐
              │     Answer       │
              └──────────────────┘
```

### Ingestion (offline)

Each document is processed through two parallel pipelines at ingest time:

1. **Summary generation** — An LLM call produces a concise 3-5 sentence summary
   covering the document's key topics and findings.
2. **Summary storage** — The summary text is stored in PostgreSQL
   (`documents.analysis_summary`) and also embedded as a
   `ChunkType.SUMMARY` vector in Milvus in the same collection (with
   `chunk_type = "summary"`).
3. **Full chunking** — The document is chunked, embedded, and indexed normally
   (as before), with each chunk's `source_path` pointing to the original file.

### Two-Stage Retrieval (online)

When `hierarchical_mode = true` (default):

| Step | Description |
|------|-------------|
| **①** | Query is embedded using the text embedding model. |
| **②** | **Stage 1** — Search against `chunk_type == "summary"` vectors to identify the top-N most relevant documents (N = `SUMMARY_TOP_K`, default 3). |
| **③** | Extract the `source_path` values from the matched summary vectors. |
| **④** | **Stage 2** — Search against non-summary chunks filtered by `source_path in [matched docs] and chunk_type != "summary"`. |
| **⑤** | Results are fused via RRF (if multiple enhanced queries) and reranked using the reranker. |
| **⑥** | Return the top-K document chunks — no summary vectors leak into results. |

### Interaction with Query Enhancements

Hierarchical mode is independent of query enhancements (HyDE, sub-queries,
stepback). Both can be active simultaneously. When multiple enhanced queries
are generated, each one independently goes through the two-stage pipeline:

```
Enhanced queries:
  ┌─ hyde_doc_1 ──→ stage1(summaries) → stage2(chunks) ─┐
  ├─ hyde_doc_2 ──→ stage1(summaries) → stage2(chunks) ─┤  RRF
  ├─ sub_query_1 ─→ stage1(summaries) → stage2(chunks) ─┤ fusion
  ├─ sub_query_2 ─→ stage1(summaries) → stage2(chunks) ─┤
  └─ original ────→ stage1(summaries) → stage2(chunks) ─┘
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HIERARCHICAL_MODE` | `true` | Enable two-stage coarse-to-fine search |
| `SUMMARY_TOP_K` | `3` | Number of summary matches in stage 1 |
| `GENERATE_SUMMARY` | `true` | Generate document summary at ingest time |

Override per request via the API:

```bash
# Enable hierarchical search
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "hierarchical": true}'

# Disable hierarchical search (flat vector search)
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "hierarchical": false}'
```

### Test

```bash
POSTGRES_HOST=localhost MILVUS_HOST=localhost .venv/bin/python tests/test_hierarchical.py
```
