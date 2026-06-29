# Issues Found

Bugs and design issues discovered during development, all now fixed.

## Fixed

### `tiers.py`, `ingest.py` — Hypothetical-question generation was sequential

Hypothetical questions per chunk were generated one chunk at a time. For a
100-chunk document with 3 questions per chunk, this was 300 serial LLM calls.

**Fix:** Wrapped in `ThreadPoolExecutor(max_workers=8)` so chunks are processed
concurrently.

---

### `tiers.py`, `api.py` — Instant-tier standalone images had no embeddings

Standalone images (`.png`, `.jpg`) ingested at instant tier skipped both OCR
and multimodal embedding. The resulting chunk had empty text and no vector,
making it unreachable by search.

**Fix:** When an instant-tier document has images but no extractable text,
multimodal embedding is enabled dynamically. Text-bearing files (PDF with text
layer, `.md`, `.txt`) still skip multimodal embedding for speed.

---

### `chunk.py` — Dead `_image_chunks()` would crash on typed bytes

`_image_chunks()` iterated `doc.images` (a `list[bytes]`) as `(bytes, str)`
tuples. Would `TypeError` if ever called. It was also unreachable — all
ingestion paths strip images before `chunk()`.

**Fix:** Removed the dead function entirely.

---

### `index.py` — Monolithic 370-line file mixing Milvus, BM25, and utilities

`index.py` contained Milvus client creation, collection management, chunk
indexing, BM25 build/load, and a utility function. Tight coupling made
testing and reuse harder.

**Fix:** Split into `milvus.py` (Milvus client + collection management) and
`bm25.py` (BM25 persistence).

---

### `embed.py` — litellm dependency for simple OpenAI-compatible calls

`embed.py` used `litellm.embedding()` which pulled in the entire litellm
library (30+ dependencies) for simple HTTP POST to `/embeddings`.

**Fix:** Replaced with direct `httpx` calls. `litellm` removed from lock file.

---

### Old `tiers.py` + `text_pipeline.py` + `image_pipeline.py` + `ingest.py` — Duplicated ingestion paths

Four files implemented overlapping ingestion logic. Old `ingest.py` was a
dead file. `text_pipeline.py` and `image_pipeline.py` duplicated chunking,
embedding, and indexing. Old `tiers.py` defined tier constants inline.

**Fix:** Consolidated into `ingest.py` (file-based, registry, duplicate
detection), `process.py` (API document processing), and `tiers.py` (config
only). All imports updated.

---

### `db.py` — Stale `_PIPELINE_SOURCE_ID` module-level cache

`_ensure_pipeline_source()` cached the Source ID in a module global,
requiring `reset_pipeline_source()` in test teardowns.

**Fix:** Removed the cache. Function always queries the DB. Tests no longer
need the reset call.

---

### `extract.py` — Duplicated `extract()` and `extract_fast()` functions

Two functions with nearly identical signatures. `extract_fast()` skipped
PDF rendering; `extract()` rendered at DPI. Any caller wanting the "fast"
path had to know about both functions.

**Fix:** Merged into single `extract(path, fast=False)`. All callers updated.

---

### `query.py` — Used `urllib` for HTTP calls

`query.py`'s `_chat()` function used stdlib `urllib` with manual JSON
encoding/decoding, error handling, and no timeout support.

**Fix:** Replaced with `httpx` — cleaner API, timeout support, better
error messages. Consistent with the rest of the codebase.

---

### `api.py` — Broken step-level progress tracking, 482-line file

`_execute_job()` tracked 11 ingestion steps through `ProgressTracker`, but
`process_text()` and `process_image()` no longer accepted a `progress`
parameter. The per-step tracking was silently dead code. The file was 482
lines, the largest in the pipeline.

**Fix:** Removed the broken step tracking. `_execute_job()` now simply
iterates files, calls `extract()` + `process_text/process_image()`, and
collects results. File trimmed to ~320 lines.
