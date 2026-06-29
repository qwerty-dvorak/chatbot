# Two-Tier Ingestion System

The RAG pipeline supports two levels of processing depth, trading speed for quality.
Choose the tier based on the use case.

## Tier Summary

| | Instant | Slow |
|---|---------|------|
| **Use case** | Chat-session file upload | Single-file deep processing |
| **Latency target** | < 2 s/page | Minutes |
| **PDF extraction** | pypdf text layer | Text plus OCR for extracted images |
| **Text embedding** | ✓ | ✓ |
| **Multimodal embedding** | ✗ | ✓ |
| **Chunking strategy** | `recursive` | `sentence_window` |
| **Hypothetical questions** | 0 (disabled) | 2 per chunk |
| **Query enhancements** | None | HyDE only |
| **Reranker** | ✗ | ✓ |
| **LLM for enhancements** | — | `CHAT_*` |

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

## Configuration

Tier defaults are defined in `pipeline/tiers.py` as `INSTANT_OPTIONS` and
`SLOW_OPTIONS`.  All values can be tuned without changing code by adjusting the
module-level constants or by subclassing `IngestOptions`.

Environment variables that affect tier behaviour:

| Variable | Used by | Effect |
|----------|---------|--------|
| `CHAT_BASE_URL` | slow search | LLM endpoint for query enhancements |
| `CHAT_MODEL` | slow search | Model name for query enhancements |
| `OCR_PDF_DPI` | future page-rendering extractor | Reserved OCR render DPI |
| `CHUNK_SIZE` | all tiers | Child chunk size in characters |
| `CHUNK_OVERLAP` | all tiers | Overlap between adjacent chunks |
| `HYPOTHETICAL_QUESTIONS_PER_CHUNK` | slow | Fallback if not overridden in options |

## Ingestion Stats

Every ingest call returns a stats dict:

```json
{
  "tier": "slow",
  "files_processed": 3,
  "files_skipped_duplicate": 1,
  "chunks_created": 87,
  "embeddings_indexed": 174,
  "hyde_generated": 261,
  "question_chunks_indexed": 87
}
```

Note: `embeddings_indexed` includes both document chunk vectors AND hypothetical
question chunk vectors. For example, 87 document chunks + 87 question vectors
(3 per chunk for 29 chunks at the slow tier) = 174 total. The `hyde_generated`
and `question_chunks_indexed` fields break down the counts separately.


