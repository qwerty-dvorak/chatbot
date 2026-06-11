# RAG Pipeline API

Base URL: `http://localhost:8093`

OpenAPI:

- Swagger UI: `http://localhost:8093/docs`
- Schema: `http://localhost:8093/openapi.json`

## Ingestion Contract

Ingestion is asynchronous. `POST /v1/ingest` persists the uploads, creates a
durable SQLite job, and returns `202 Accepted`. A single background worker
processes queued jobs in creation order. The HTTP request does not remain open
while extraction, OCR, embedding, and indexing run.

Job statuses:

| Status | Meaning |
|--------|---------|
| `queued` | Persisted and waiting for the worker |
| `running` | Claimed by the ingestion worker |
| `succeeded` | Pipeline completed; `result` contains statistics |
| `failed` | Pipeline raised an error; `error` contains a traceback |
| `cancelled` | Cancelled before the worker claimed it |

Uploads and queue state are stored under `INGESTION_DATA_DIR`. The default is
`./data/ingestion`; the Docker container uses the mounted `/app/data` tree.
Jobs left in `running` state by a process restart are requeued at startup.

## Health

### `GET /health/live`

Process liveness. This endpoint does not test Milvus.

```json
{"status": "ok"}
```

### `GET /health/ready`

Checks Milvus, the worker, and queue counters. Returns `503` when Milvus is not
available.

```json
{
  "status": "ready",
  "worker_running": true,
  "queue": {
    "queued": 0,
    "running": 1,
    "succeeded": 12,
    "failed": 0,
    "cancelled": 0
  },
  "collections": 2
}
```

### `GET /health`

Compatibility health endpoint. It reports configuration and queue state but
does not fail when Milvus is unavailable. Use `/health/ready` for readiness
probes.

```json
{
  "status": "ok",
  "milvus_host": "test-milvus",
  "milvus_port": 19530,
  "worker_running": true,
  "queue": {
    "queued": 0,
    "running": 0,
    "succeeded": 5,
    "failed": 0,
    "cancelled": 0
  }
}
```

## Queue Ingestion

### `POST /v1/ingest`

Persist one or more files and enqueue ingestion.

Content type: `multipart/form-data`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `files` | file[] | required | One or more supported documents |
| `tier` | enum | `slow` | `instant`, `slow`, or `global` |
| `strategy` | enum/null | tier default | `recursive`, `sentence_window`, or `hierarchical` |
| `hypothetical_questions` | bool/null | tier default | Explicitly enable or disable index-time question generation |

Tier defaults:

| Tier | Extraction | Image embedding | Chunking | Hypothetical questions |
|------|------------|-----------------|----------|------------------------|
| `instant` | Existing text layer | No | `recursive` | Disabled (0 per chunk) |
| `slow` | Text plus OCR for extracted images | Yes | `sentence_window` | 2 per text chunk |
| `global` | Text plus OCR for extracted images | Yes | `hierarchical` | 3 per text chunk |

Example:

```bash
curl -i -X POST http://localhost:8093/v1/ingest \
  -F "files=@report.pdf" \
  -F "files=@notes.md" \
  -F "tier=slow"
```

Response: `202 Accepted`

```json
{
  "id": "daaa89d8131946138f87b84ef9fa291b",
  "kind": "ingest",
  "status": "queued",
  "files": ["report.pdf", "notes.md"],
  "request": {
    "tier": "slow",
    "strategy": null,
    "hypothetical_questions": null
  },
  "result": null,
  "error": null,
  "created_at": "2026-06-10T00:10:32.123456+00:00",
  "started_at": null,
  "completed_at": null,
  "links": {
    "self": "/v1/ingestions/daaa89d8131946138f87b84ef9fa291b",
    "collection": "/v1/ingestions"
  }
}
```

Poll the job:

```bash
curl http://localhost:8093/v1/ingestions/daaa89d8131946138f87b84ef9fa291b
```

A successful result includes persistent source paths that can later be passed
to `/v1/promote`:

```json
{
  "status": "succeeded",
  "result": {
    "tier": "slow",
    "files_processed": 2,
    "files_failed": 0,
    "files_skipped_duplicate": 0,
    "chunks_created": 84,
    "embeddings_indexed": 111,
    "errors": [],
    "source_paths": [
      "/app/data/ingestion/uploads/daaa89d8131946138f87b84ef9fa291b/notes.md",
      "/app/data/ingestion/uploads/daaa89d8131946138f87b84ef9fa291b/report.pdf"
    ]
  }
}
```

Note: `embeddings_indexed` includes both document chunk vectors and hypothetical
question vectors. For example, 84 document chunks + 27 question vectors = 111
total. The `hyde_generated` field in the per-file results shows the question
count.

### `GET /v1/ingestions`

List recent ingestion and promotion jobs.

Query parameters:

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `status` | enum/null | null | Filter by one job status |
| `limit` | int | `50` | Number of jobs, from 1 to 200 |

```bash
curl "http://localhost:8093/v1/ingestions?status=failed&limit=20"
```

```json
{
  "jobs": [],
  "total": 0
}
```

### `GET /v1/ingestions/{job_id}`

Return one complete job record. Returns `404` for an unknown identifier.

### `DELETE /v1/ingestions/{job_id}`

Cancel a `queued` job. Running jobs cannot be interrupted because extraction
and index writes do not provide transaction-safe cancellation points.

- `200`: queued job changed to `cancelled`
- `404`: job does not exist
- `409`: job is running or already terminal

## Queue Promotion

### `POST /v1/promote`

Queue replacement of a document's existing vectors with a higher-quality tier.
The source file must still exist in persistent storage.

```bash
curl -X POST http://localhost:8093/v1/promote \
  -H "Content-Type: application/json" \
  -d '{
    "source_path": "/app/data/ingestion/uploads/JOB_ID/report.pdf",
    "to_tier": "global",
    "delete_old_chunks": true
  }'
```

Response: `202 Accepted` with the same job resource shape as ingestion and
`"kind": "promote"`.

Promotion runs through the same single worker. When `delete_old_chunks` is
true, it removes matching Milvus rows (both document chunks and hypothetical
question chunks with matching `source_path`) and matching BM25 chunks before
indexing the new representation.

## Search

### `POST /v1/search`

Search remains synchronous because it is expected to be short-lived. All
features (HyDE, sub-queries, stepback, hierarchical index, reranker) are
**on by default** — send the simplest possible request for maximum quality.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Non-empty search query |
| `top_k` | int | `5` | Result count, from 1 to 100 |
| `mode` | enum | `hybrid` | `hybrid`, `vector`, or `bm25` |
| `use_reranker` | bool | `true` | Cross-encoder reranker on merged results |
| `hierarchical` | bool | `true` | Two-stage coarse-to-fine search |
| `hyde` | bool | `true` | Generate hypothetical answer documents |
| `sub_queries` | bool | `true` | Decompose complex queries into sub-questions |
| `stepback` | bool | `true` | Abstract query to broader foundational question |
| `tier` | enum | `null` | Apply tier defaults (overrides individual flags) |
| `enhancements` | string | `null` | Comma-separated override: `hyde,sub_queries,stepback` |

**Flag resolution order** (later wins):
1. Individual flags (`hyde`, `sub_queries`, `stepback`) — all `true` by default
2. `tier` — if set, replaces individual flags with tier's predefined set
3. `enhancements` string — if set, replaces everything above

Disable specific features by passing `false`:

```bash
# Everything on (default — just omit flags)
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "self-attention complexity"}'

# Only hyde, no sub-queries or stepback
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "sub_queries": false, "stepback": false}'

# Only vector search, no enhancements, no reranker, no hierarchical
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "mode": "vector", "hyde": false}'

# Use tier defaults
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "tier": "global"}'

# explicit enhancements string overrides everything
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "enhancements": "hyde,stepback"}'
```

Tier search defaults:

| Tier | Enhancements | Reranker |
|------|--------------|----------|
| `instant` | none | off |
| `slow` | `hyde` | on |
| `global` | `hyde,sub_queries,stepback` | on |

**Query-to-query resolution:** The search pipeline automatically resolves
hypothetical question vector hits to their source document chunks. If a query
vector matches a `hypothetical_question` chunk in Milvus, the result is
replaced with its parent chunk (found via `parent_id`). The `method` field
in the response is set to `"query_to_query"` for these results. This happens
transparently — the caller always receives document chunks, never raw question
vectors.

```bash
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does self-attention scale?",
    "top_k": 5,
    "mode": "hybrid"
  }'
```

```json
{
  "query": "How does self-attention scale?",
  "enhanced_queries": [
    "The self-attention mechanism has a computational complexity that scales quadratically with the input sequence length...",
    "How does the computational cost of self-attention grow with sequence length?",
    "What are the fundamental complexity classes and scaling rules for attention mechanisms in transformer architectures?",
    "How does self-attention scale?"
  ],
  "retrieval_mode": "hybrid",
  "use_reranker": true,
  "hierarchical": true,
  "results": [
    {
      "rank": 1,
      "score": 0.91,
      "method": "reranked",
      "source": "/app/data/ingestion/uploads/JOB_ID/paper.pdf",
      "chunk_type": "child",
      "text": "Self-attention has quadratic sequence-length complexity...",
      "has_image": false,
      "metadata": {
        "filename": "paper.pdf",
        "ingestion_tier": "global"
      }
    }
  ],
  "total": 1
}
```

**Response fields:**

| Field | Type | Description |
|-------|------|-------------|
| `query` | string | Original query |
| `enhanced_queries` | list[string] | All query strings searched (HyDE docs, sub-queries, stepback, original) |
| `retrieval_mode` | string | Mode used: `hybrid`, `vector`, or `bm25` |
| `use_reranker` | bool | Whether reranker was applied |
| `hierarchical` | bool | Whether two-stage summary-based search was used |
| `results` | list[object] | Ranked result chunks |
| `total` | int | Number of results returned |

Possible `method` values in search results:

| Method | Meaning |
|--------|---------|
| `vector` | Vector similarity match (inner product) |
| `bm25` | BM25 keyword match |
| `hybrid` | RRF fusion of vector + BM25 |
| `reranked` | Cross-encoder re-scored by reranker |
| `query_to_query` | Hypothetical question vector match, resolved to parent chunk |

### `GET /v1/search?q=...`

Quick search via query parameter (same logic as POST, but returns raw
`SearchResult` objects for CLI testing).

```bash
curl "http://localhost:8093/v1/search?q=what+is+the+Acheron+Trough"
```

## Collection Administration

### `GET /v1/collections`

Returns Milvus collection names.

```json
["rag_text_chunks", "rag_image_chunks"]
```

### `DELETE /v1/collections/{name}`

Drops a complete Milvus collection. This is destructive and does not remove
the BM25 index, registry, uploaded files, or job history.

## Ingestion Job Result Fields

Per-file results returned in the job's `results` array:

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| `document_id` | string | `"fcfd2559-..."` | PostgreSQL `documents` UUID |
| `object_key` | string | `"abc123..."` | SHA-256 key in object store |
| `chunks_created` | int | `9` | Number of document chunks created |
| `embeddings_indexed` | int | `36` | Total vectors indexed (doc + question) |
| `hyde_generated` | int | `27` | Total hypothetical questions generated |
| `question_chunks_indexed` | int | `27` | Number of question vectors indexed |

## Errors

FastAPI validation errors use `422`. Runtime failures use:

| Status | Meaning |
|--------|---------|
| `404` | Job or promotion source not found |
| `409` | Requested job transition is not allowed |
| `422` | Invalid request field, tier, strategy, mode, or bound |
| `500` | Search or collection operation failed |
| `503` | Readiness check cannot reach Milvus |

Background pipeline failures do not change the polling endpoint's HTTP status.
The job resource returns `200` with `"status": "failed"` and diagnostic text in
`error`.
