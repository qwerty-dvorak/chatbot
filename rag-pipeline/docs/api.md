# RAG Pipeline API Reference

Base URL: `http://localhost:8093`

Interactive docs (Swagger UI): `http://localhost:8093/docs`

---

## `GET /health`

Returns service health and Milvus connection details.

**Response**

```json
{
  "status": "ok",
  "milvus_host": "localhost",
  "milvus_port": 19530
}
```

---

## `POST /v1/ingest`

Upload one or more files and ingest them into the RAG index.

**Parameters** (multipart form)

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `files` | file[] | required | Files to ingest |
| `tier` | string | `slow` | Processing depth: `instant`, `slow`, or `global` |
| `strategy` | string | null | Chunk strategy override: `recursive`, `sentence_window`, `hierarchical` |
| `hypothetical_questions` | bool | `false` | Add hypothetical questions at index time |

When `tier` is provided, the tier's default chunk strategy and hypothetical-question
settings apply.  Explicit `strategy` or `hypothetical_questions` values override
the tier defaults.

**Tier behaviour:**

| Tier | Extraction | Multimodal | Chunking | Hyp. Qs |
|------|-----------|-----------|---------|---------|
| `instant` | PyMuPDF text (no OCR) | No | recursive | 0 |
| `slow` | OCR @ 150 DPI | Yes | sentence_window | 2 |
| `global` | OCR @ 200 DPI | Yes | hierarchical | 3 |

**Example — instant upload for chat RAG**

```bash
curl -X POST http://localhost:8093/v1/ingest \
  -F "files=@report.pdf" \
  -F "tier=instant"
```

**Example — slow ingestion with explicit strategy**

```bash
curl -X POST http://localhost:8093/v1/ingest \
  -F "files=@manual.pdf" \
  -F "files=@notes.md" \
  -F "tier=slow" \
  -F "strategy=recursive"
```

**Example — global batch with hypothetical questions**

```bash
curl -X POST http://localhost:8093/v1/ingest \
  -F "files=@paper.pdf" \
  -F "tier=global"
```

**Response**

```json
{
  "status": "ok",
  "files": ["report.pdf"],
  "tier": "instant",
  "stats": {
    "tier": "instant",
    "files_processed": 1,
    "files_skipped_duplicate": 0,
    "chunks_created": 24,
    "embeddings_indexed": 24
  }
}
```

---

## `POST /v1/promote`

Re-ingest a document at a higher tier, deleting the old chunks.

**Request body (JSON)**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source_path` | string | required | Absolute path to the file on disk |
| `to_tier` | string | required | Target tier: `instant`, `slow`, or `global` |
| `delete_old_chunks` | bool | `true` | Delete existing Milvus vectors before re-ingesting |

**Example**

```bash
curl -X POST http://localhost:8093/v1/promote \
  -H "Content-Type: application/json" \
  -d '{
    "source_path": "/data/uploads/report.pdf",
    "to_tier": "global",
    "delete_old_chunks": true
  }'
```

**Response**

```json
{
  "status": "ok",
  "stats": {
    "tier": "global",
    "files_processed": 1,
    "files_skipped_duplicate": 0,
    "chunks_created": 142,
    "embeddings_indexed": 142,
    "deleted_text_chunks": 24,
    "deleted_image_chunks": 8
  }
}
```

**Error — file not found**

```json
{
  "detail": "Source file not found: '/data/uploads/report.pdf'"
}
```

---

## `POST /v1/search`

Search the RAG index.

**Request body (JSON)**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | string | required | Search query |
| `top_k` | int | `5` | Maximum results to return |
| `mode` | string | `hybrid` | `hybrid`, `vector`, or `bm25` |
| `use_reranker` | bool | `true` | Run reranker after fusion |
| `tier` | string | null | Set search defaults from tier: `instant`, `slow`, `global` |
| `enhancements` | string | null | Comma-separated enhancement names (overrides tier) |

**Tier search defaults:**

| Tier | Enhancements | Reranker | LLM |
|------|-------------|---------|-----|
| `instant` | none | no | — |
| `slow` | hyde | yes | CHAT_* |
| `global` | hyde, sub_queries, stepback | yes | CHATBOT_LLM_* |

**Example — instant search (no enhancements)**

```bash
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "transformer attention mechanism", "tier": "instant"}'
```

**Example — global search (all enhancements)**

```bash
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "how does self-attention scale with sequence length",
    "top_k": 10,
    "tier": "global"
  }'
```

**Example — explicit enhancements (override tier)**

```bash
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "attention mechanism",
    "enhancements": "hyde,sub_queries",
    "use_reranker": true
  }'
```

**Response**

```json
{
  "query": "transformer attention mechanism",
  "results": [
    {
      "rank": 1,
      "score": 0.0189,
      "method": "reranked",
      "source": "/data/paper.pdf",
      "chunk_type": "child",
      "text": "Self-attention allows each position in the sequence...",
      "has_image": false,
      "metadata": {
        "ingestion_tier": "global",
        "filename": "paper.pdf"
      }
    }
  ],
  "total": 1
}
```

---

## `GET /v1/collections`

List all Milvus collections.

**Response**

```json
["rag_text_chunks", "rag_image_chunks"]
```

---

## `DELETE /v1/collections/{name}`

Drop a Milvus collection by name.  Use with caution — all indexed data is lost.

**Response**

```json
{"dropped": "rag_text_chunks"}
```

---

## Error responses

All endpoints return standard HTTP status codes:

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `404` | Resource not found (e.g. file for `/v1/promote`) |
| `422` | Validation error (e.g. unknown tier name) |
| `500` | Internal error — check server logs |

Error body:

```json
{"detail": "error message"}
```
