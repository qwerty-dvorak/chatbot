# Three-Tier Ingestion System

The RAG pipeline supports three levels of processing depth, trading speed for quality.
Choose the tier based on the use case, then promote documents to higher tiers in the
background when time allows.

## Tier Summary

| | Instant | Slow | Global |
|---|---------|------|--------|
| **Use case** | Chat-session file upload | Single-file deep processing | Global knowledge base |
| **Latency target** | < 2 s/page | Minutes | Hours (batch) |
| **PDF extraction** | pypdf text layer | Text plus OCR for extracted images | Text plus OCR for extracted images |
| **Text embedding** | ✓ | ✓ | ✓ |
| **Multimodal embedding** | ✗ | ✓ | ✓ |
| **Chunking strategy** | `recursive` | `sentence_window` | `hierarchical` |
| **Hypothetical questions** | 0 (disabled) | 2 per chunk | 3 per chunk |
| **Query enhancements** | None | HyDE only | HyDE + sub-queries + stepback |
| **Reranker** | ✗ | ✓ | ✓ |
| **LLM for enhancements** | — | `CHAT_*` | `CHAT_*` |

## Tier Details

### Instant

Optimised for immediate availability in user chat sessions.

**Ingestion:**
- PDFs: reads the existing text layer with pypdf and skips OCR.
- Images: stored as raw bytes but NOT multimodal-embedded (no GPU call).
- Chunking: `recursive` (parent 2048 chars → children 512 chars).
- No hypothetical-question augmentation (avoids LLM round-trips at ingest time).

**Search:**
- Raw query only — no HyDE, no sub-queries.
- No reranker — direct vector + BM25 fusion result.
- Fastest possible retrieval.

**When to use:**
- A user uploads a file mid-chat and needs immediate answers from it.
- The file is short (< 20 pages) and likely has a text layer (reports, articles, code).

---

### Slow

Full quality for a single file; suitable for document-management uploads.

**Ingestion:**
- PDFs: preserve pypdf text and OCR images that pypdf can extract from the document.
- Both text embedding (Track A) and multimodal/image embedding (Track B) run.
- Chunking: `sentence_window` (one sentence per chunk + N surrounding sentences as context).
- 2 hypothetical questions generated per text chunk at index time.

**Search:**
- HyDE (Hypothetical Document Embeddings) replaces the raw query with a generated passage.
- Reranker re-scores the top-K results.

**When to use:**
- A user explicitly uploads a document to their knowledge base.
- You want both text and visual content indexed (diagrams, tables, charts).
- Single-file processing (not batch).

---

### Global

Maximum quality; designed for batch processing of a global knowledge base.

**Ingestion:**
- Uses the same text-plus-extracted-image OCR path as slow tier.
- Both embedding tracks are enabled.
- Chunking: `hierarchical` (paragraph-grouped summaries + fine-grained children).
- 3 hypothetical questions per chunk using the default chat model.

**Search:**
- All three query enhancements applied:
  1. **HyDE** — generates a hypothetical answer passage and embeds it.
  2. **Sub-queries** — decomposes the question into 2-4 focused sub-questions.
  3. **Stepback** — reformulates to a broader question for better recall.
- Enhancements use the configured local `CHAT_*` endpoint.
- Reranker always on.

**When to use:**
- Batch ingestion of large document collections.
- Permanent global knowledge base that many users search.
- Quality matters more than speed.

## Tier Promotion

Documents can be promoted to a higher tier without re-uploading.  Old Milvus
vectors are deleted before the new, richer ones are indexed, preventing duplicates.

### Via API

```bash
curl -X POST http://localhost:8093/v1/promote \
  -H "Content-Type: application/json" \
  -d '{
    "source_path": "/data/uploads/report.pdf",
    "to_tier": "global",
    "delete_old_chunks": true
  }'
```

### Via Python

```python
from pipeline.tiers import promote_document, IngestionTier

stats = promote_document(
    "/data/uploads/report.pdf",
    to_tier=IngestionTier.GLOBAL,
    delete_old_chunks=True,
)
print(stats)
# {
#   "tier": "global",
#   "files_processed": 1,
#   "chunks_created": 142,
#   "embeddings_indexed": 142,
#   "deleted_text_chunks": 38,
#   "deleted_image_chunks": 12,
# }
```

### Common promotion patterns

```
Chat upload → session ends → promote to slow (better search for future sessions)
Chat upload → admin decision → promote to global (add to shared knowledge base)
Slow → time passes → promote to global (batch quality upgrade)
```

The API always queues promotion through the same durable worker used for
ingestion. Poll the returned `/v1/ingestions/{id}` resource for completion.

## Configuration

Tier defaults are defined in `pipeline/tiers.py` as `INSTANT_OPTIONS`, `SLOW_OPTIONS`,
and `GLOBAL_OPTIONS`.  All values can be tuned without changing code by adjusting the
module-level constants or by subclassing `IngestOptions`.

Environment variables that affect tier behaviour:

| Variable | Used by | Effect |
|----------|---------|--------|
| `CHAT_BASE_URL` | slow/global search | LLM endpoint for query enhancements |
| `CHAT_MODEL` | slow/global search | Model name for query enhancements |
| `OCR_PDF_DPI` | future page-rendering extractor | Reserved OCR render DPI |
| `CHUNK_SIZE` | all tiers | Child chunk size in characters |
| `CHUNK_OVERLAP` | all tiers | Overlap between adjacent chunks |
| `HYPOTHETICAL_QUESTIONS_PER_CHUNK` | slow/global | Fallback if not overridden in options |

## Ingestion Stats

Every ingest and promote call returns a stats dict:

```json
{
  "tier": "slow",
  "files_processed": 3,
  "files_skipped_duplicate": 1,
  "chunks_created": 87,
  "embeddings_indexed": 87
}
```

Promote adds:

```json
{
  "deleted_text_chunks": 38,
  "deleted_image_chunks": 12
}
```
