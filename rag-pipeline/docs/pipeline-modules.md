# Pipeline Module Reference

## `pipeline/models.py`

Core data classes and enumerations shared across all modules.

### `ChunkType`

```python
class ChunkType(str, Enum):
    TEXT                 = "text"                  # plain text chunk
    IMAGE                = "image"                 # page image from PDF or image file
    PARENT               = "parent"                # large parent for recursive/hierarchical
    CHILD                = "child"                 # small child of a PARENT
    SENTENCE_WINDOW      = "sentence_window"       # one sentence + surrounding context
    SUMMARY              = "summary"               # summary chunk (hierarchical)
    HYPOTHETICAL_QUESTION = "hypothetical_question" # generated question from a chunk
```

`HYPOTHETICAL_QUESTION` chunks are created during the ingestion HyDE step.
Each one represents a question that the parent chunk would answer. These chunks
are embedded and indexed into Milvus as separate vectors. At search time, any
result with this chunk type is resolved to its parent chunk via `parent_id`.

`HYPOTHETICAL_QUESTION` chunks always have `parent_id` set to the UUID of the
source document chunk they were generated from. They always have `image_data =
None` (text-only).

### `IngestionTier`

```python
class IngestionTier(str, Enum):
    INSTANT = "instant"   # fast, text-only, no OCR, no hypothetical questions
    SLOW    = "slow"      # full OCR + multimodal, 2 hypothetical questions/chunk
    GLOBAL  = "global"    # max quality, all enhancements, 3 hypothetical questions/chunk
```

### `RawDocument`

```python
@dataclass
class RawDocument:
    path: str
    content_type: ContentType               # TEXT, IMAGE, or PDF
    text: str                               # full extracted text
    images: list[bytes]                     # raw image bytes
    metadata: dict                          # filename, extension, size_bytes, ...
```

### `Chunk`

```python
@dataclass
class Chunk:
    id: str                                 # UUID4 hex
    source_path: str                        # absolute path of the source file
    text: str                               # chunk text (OCR text for image chunks,
                                            # or generated question text for HYPOTHETICAL_QUESTION)
    chunk_type: ChunkType
    metadata: dict                          # includes "ingestion_tier"
    parent_id: Optional[str]               # set for CHILD, SENTENCE_WINDOW, HYPOTHETICAL_QUESTION
    window_text: Optional[str]             # surrounding context (sentence_window)
    children_ids: list[str]                # set on PARENT chunks
    image_data: Optional[bytes]            # raw PNG bytes for image chunks, None for hypothetical questions
```

### `EmbeddedChunk`

```python
@dataclass
class EmbeddedChunk:
    chunk: Chunk
    embedding: list[float]
    is_multimodal: bool = False            # True → image_collection
```

### `SearchResult`

```python
@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    rank: int
    retrieval_method: str                  # "vector", "bm25", "hybrid", "reranked", "query_to_query"
```

`retrieval_method` includes `"query_to_query"` when the result was obtained by
resolving a hypothetical question hit to its parent document chunk.

---

## `pipeline/config.py`

Global singleton `cfg: Config` loaded from `.env` via `python-dotenv`.

Key fields and their env vars:

| Field | Env var | Default |
|-------|---------|---------|
| `chat_base_url` | `CHAT_BASE_URL` | `http://localhost:9000/v1` |
| `chat_model` | `CHAT_MODEL` | `openai/mock-chat` |
| `embedding_base_url` | `EMBEDDING_BASE_URL` | `http://localhost:9001/v1` |
| `text_embedding_model` | `TEXT_EMBEDDING_MODEL` | `nvidia/llama-embed-nemotron-8b` |
| `text_embedding_dim` | `TEXT_EMBEDDING_DIM` | `4096` |
| `multimodal_embedding_base_url` | `MULTIMODAL_EMBEDDING_BASE_URL` | `http://localhost:9002` |
| `multimodal_embedding_dim` | `MULTIMODAL_EMBEDDING_DIM` | `4096` |
| `reranker_base_url` | `RERANKER_BASE_URL` | `http://localhost:9003` |
| `ocr_base_url` | `OCR_BASE_URL` | `http://localhost:9004/v1` |
| `ocr_pdf_dpi` | `OCR_PDF_DPI` | `150` |
| `milvus_host` | `MILVUS_HOST` | `localhost` |
| `milvus_port` | `MILVUS_PORT` | `19530` |
| `text_collection` | `TEXT_COLLECTION` | `rag_text_chunks` |
| `image_collection` | `IMAGE_COLLECTION` | `rag_image_chunks` |
| `bm25_index_path` | `BM25_INDEX_PATH` | `./data/bm25_index.pkl` |
| `ingestion_data_dir` | `INGESTION_DATA_DIR` | `./data/ingestion` |
| `ingestion_poll_interval` | `INGESTION_POLL_INTERVAL` | `0.5` |
| `chunk_strategy` | `CHUNK_STRATEGY` | `recursive` |
| `chunk_size` | `CHUNK_SIZE` | `512` |
| `chunk_overlap` | `CHUNK_OVERLAP` | `64` |
| `retrieval_top_k` | `RETRIEVAL_TOP_K` | `20` |
| `rerank_top_k` | `RERANK_TOP_K` | `5` |
| `hybrid_alpha` | `HYBRID_ALPHA` | `0.5` |
| `query_enhancements` | `QUERY_ENHANCEMENTS` | `"hyde,sub_queries,stepback"` |
| `hypothetical_questions_per_chunk` | `HYPOTHETICAL_QUESTIONS_PER_CHUNK` | `3` |

---

## `pipeline/extract.py`

### `extract(path: str) -> RawDocument`

Extract plain text, PDF text and extractable embedded images, or standalone image
bytes. PDF extraction uses pypdf and does not render every page.

### `extract_fast(path: str) -> RawDocument`

For PDFs, extracts only the text layer and does not enumerate embedded images.
Instant-tier ingestion uses this path to avoid image extraction overhead.

---

## `pipeline/ocr.py`

### `ocr_image(image_bytes: bytes) -> str`

Send one page image to PaddleOCR-VL-1.6 via `/v1/chat/completions`. Uses the
"OCR:" task prompt.

### `ocr_pages(page_images: list[bytes], max_workers: int = 2) -> list[str]`

OCR a list of page images concurrently (up to 2 workers). Returns a list of
the same length; failed pages get `""`. Order is preserved.

---

## `pipeline/chunk.py`

### `chunk(doc: RawDocument, strategy: str | None = None) -> list[Chunk]`

Main entry point. Returns text chunks (from the chosen strategy) plus one
`ChunkType.IMAGE` chunk per entry in `doc.images`.

**Strategies:**

`recursive` — Two-level: PARENT chunks (2048 chars) split into CHILD chunks
(512 chars). Retrieval fetches children; context comes from parents.

`sentence_window` — One `SENTENCE_WINDOW` chunk per sentence. `window_text`
contains N surrounding sentences (default N=3) for context retrieval.

`hierarchical` — Paragraph-grouped PARENT chunks (up to 2048 chars) with CHILD
chunks (512 chars). Similar to recursive but uses paragraph boundaries for
better semantic coherence.

---

## `pipeline/embed.py`

### `embed_text(chunks: list[Chunk], batch_size: int = 32) -> list[EmbeddedChunk]`

Batch-embed text chunks via `cfg.text_embedding_model` (`/v1/embeddings`).
Filters out any chunks with `image_data` set. Returns `EmbeddedChunk` with
`is_multimodal=False`.

This function handles both document chunks and HYPOTHETICAL_QUESTION chunks
(they are both text-only, no image_data). The caller
(`text_pipeline.py`) passes all chunks — document + hypothetical questions —
to this single function.

### `embed_multimodal(chunks: list[Chunk]) -> list[EmbeddedChunk]`

Embed image chunks via the multimodal `/pooling` endpoint. For each chunk:
1. Try image-only embedding (I mode) using `chunk.image_data`.
2. Fall back to text-only embedding (T mode) using `chunk.text` (OCR text).
3. Return `None` if both fail (filtered from output).

### `embed_all(chunks: list[Chunk]) -> list[EmbeddedChunk]`

Route text chunks to `embed_text`, image chunks to `embed_multimodal`.

---

## `pipeline/index.py`

### `connect_milvus() -> None`

Initialise the `MilvusClient` singleton. Idempotent.

### `_ensure_collection(name: str, dim: int) -> None`

Create + load a Milvus collection if it doesn't exist. Schema: 8 fields
(`id`, `source_path`, `text`, `chunk_type`, `parent_id`, `window_text`,
`metadata_json`, `embedding`). Index: `IVF_FLAT / IP`.

### `index_chunks(embedded: list[EmbeddedChunk]) -> None`

Insert embedded chunks into Milvus. Routes by `is_multimodal`:
- `False` -> `cfg.text_collection` (configured text dimension)
- `True`  -> `cfg.image_collection` (configured multimodal dimension)

Document chunks and hypothetical question chunks both go to `text_collection`
with their respective `chunk_type` values.

### `_chunk_from_hit(hit: dict) -> Chunk`

Reconstruct a `Chunk` from a MilvusClient search result dict. Handles all
`chunk_type` values including `hypothetical_question`. Used in both
`retrieve.py` (search results) and `search.py` (query-to-query resolution).

### `delete_chunks_by_source(source_path: str, collection_name: str) -> int`

Delete all chunks for a file using a Milvus scalar filter. Returns deleted
count. This deletes both document chunks and hypothetical question chunks
(since they share the same `source_path`).

### `build_bm25_index(chunks: list[Chunk]) -> BM25Okapi`

Build BM25 index from chunk texts and save to `cfg.bm25_index_path`.

### `load_bm25_index() -> tuple[BM25Okapi, list[Chunk]]`

Load BM25 index from disk. Raises `FileNotFoundError` if absent.

---

## `pipeline/retrieve.py`

### `vector_search(query_embedding, top_k, collection_name) -> list[SearchResult]`

Milvus inner-product search (cosine on normalised vectors). Returns both
document chunks and hypothetical question chunks — the caller
(`search.py`) is responsible for resolving question hits.

### `bm25_search(query: str, top_k) -> list[SearchResult]`

BM25 sparse search. Returns `[]` on `FileNotFoundError` (fresh deployment).
Hypothetical question chunks are NOT indexed in BM25 (they have no meaningful
token overlap with real documents).

### `hybrid_search(query, query_embedding, top_k) -> list[SearchResult]`

Weighted RRF pre-merge of vector + BM25 results. Weights:
`[cfg.hybrid_alpha, 1 - cfg.hybrid_alpha]`.

Returns **all** candidates from both channels (up to `2 × top_k`). No early
trimming — the reranker (see `search.py`) acts as the central fusion node,
scoring every candidate before the final top-K selection.

### `rerank(query, results, top_k) -> list[SearchResult]`

Re-score results via `{cfg.reranker_base_url}/score`. Uses original query (not
enhanced). Hypothetical question results should be resolved to parent chunks
before reranking so the reranker sees real document text.

### `_rrf_fusion(results_lists, weights=None, k=60) -> list[SearchResult]`

Weighted Reciprocal Rank Fusion. `weights` defaults to uniform 1.0.

---

## `pipeline/query.py`

### `enhance_query(query, enhancements=None) -> list[str]`

Apply enabled strategies and return a deduplicated list of query strings.
When `enhancements=None`, reads from `cfg.query_enhancements`.

Strategies:
- `hyde`: Replace query with hypothetical document text.
- `sub_queries`: Decompose query into 2-4 sub-questions plus the original.
- `stepback`: Generate a broader reformulation plus the original query.
- `hypothetical_questions`: Ignored at query time (index-time only).

### `hyde(query) -> str`

Generate a hypothetical answer passage (query-time).

### `sub_queries(query) -> list[str]`

Decompose query into 2-4 sub-questions plus the original (query-time).

### `stepback(query) -> str`

Generate a broader reformulation (query-time).

### `hypothetical_questions_for_chunk(chunk_text, n=None) -> list[str]`

Generate N questions that the chunk would answer (index-time). These strings
are consumed by `text_pipeline.py`, which wraps each into a `Chunk` object
with `chunk_type=HYPOTHETICAL_QUESTION`.

---

## `pipeline/search.py`

### `search(query, top_k, use_reranker, retrieval_mode, enhancements) -> list[SearchResult]`

Full end-to-end search pipeline. The `enhancements` parameter allows
per-request tier-specific behavior without mutating global config state.

**Steps:**
1. Connect to Milvus.
2. Generate enhanced queries via `query.py:enhance_query()`.
3. For each enhanced query string, embed it and search (vector/BM25/hybrid).
4. Merge all result lists with RRF fusion.
5. **Resolve hypothetical question hits:** For any result with
   `chunk_type == HYPOTHETICAL_QUESTION`, query Milvus by `parent_id` to
   fetch the source document chunk, replace the result's chunk, and mark
   `retrieval_method = "query_to_query"`.
6. Deduplicate by chunk ID (keep highest score).
7. Optionally rerank with the original query.
8. Best-effort parent context fetch for CHILD chunks.
9. Return top-K results.

### `format_results(results) -> str`

CLI-friendly text output of search results.

---

## `pipeline/tiers.py`

### `IngestOptions`

Frozen dataclass with all knobs that vary between tiers. Fields:
`tier`, `extract_pdf_text_directly`, `ocr_dpi`, `use_text_embedding`,
`use_multimodal_embedding`, `chunk_strategy`, `hypothetical_questions_per_chunk`,
`query_enhancements`, `use_reranker`.

### Pre-built options

- `INSTANT_OPTIONS` — no OCR, no hypothetical questions, no reranker
- `SLOW_OPTIONS` — OCR, multimodal, 2 hypothetical questions per chunk, HyDE
- `GLOBAL_OPTIONS` — all enhancements, 3 hypothetical questions per chunk

### `options_for_tier(tier: IngestionTier) -> IngestOptions`

Look up options for a tier.

### `tier_from_str(s: str) -> IngestionTier`

Parse tier string. Raises `ValueError` on unknown value.

### `ingest_tier(path, tier, skip_duplicates=True, strategy=None, hypothetical_questions=None) -> dict`

Main tiered ingestion entry point. File or directory.

### `promote_document(source_path, to_tier, delete_old_chunks=True) -> dict`

Re-ingest at a higher tier, optionally deleting old chunks first. Deletion
removes both document chunks and hypothetical question vectors for the source.

---

## `pipeline/jobs.py`

### `JobStore`

Durable SQLite repository for queue creation, oldest-first claiming, status
transitions, cancellation, restart recovery, and queue counts.

### `IngestionWorker`

Single background thread that serializes ingestion and promotion jobs.

---

## `pipeline/text_pipeline.py`

### `TextPipelineParams`

Dataclass holding all configurable pipeline parameters:
`chunk_strategy`, `chunk_size`, `chunk_overlap`, `parent_chunk_size`,
`sentence_window_size`, `embedding_model`, `embedding_dim`,
`generate_hyde`, `hyde_per_chunk`, `milvus_collection`.

### `process_document(doc, params=None, progress=None) -> dict`

Full text ingestion pipeline. Steps:

1. **store_raw** — Store file bytes in object store (SHA-256 key).
2. **db_insert** — Create Document row in chatbot-service PostgreSQL.
3. **chunk** — Split text into chunks per strategy.
4. **hyde** — Generate hypothetical questions per chunk. For each question,
   create a `Chunk` with `chunk_type=HYPOTHETICAL_QUESTION` and
   `parent_id=<source chunk UUID>`. These question chunks are collected into
   a separate list alongside the main chunk rows.
5. **persist** — Write document chunk rows (not question chunks) to
   `document_chunks` table. Question chunks are not persisted to PostgreSQL;
   they exist only as vectors in Milvus.
6. **embed** — Embed BOTH text chunks AND question chunks via
   `embed_text()`. All text-only chunks are embedded in one batch.
7. **index** — Index all embedded chunks into Milvus. Question chunks land in
   `rag_text_chunks` with `chunk_type='hypothetical_question'`.

Returns stats dict with `document_id`, `chunks_created`, `embeddings_indexed`,
`hyde_generated`, `question_chunks_indexed`, `object_key`.
## Image preprocessing and OCR

`image_preprocess.py` normalizes orientation/color and splits tall images.
`ocr.py` selects no OCR, basic Tesseract, or PaddleOCR. RunPod mode uses the
PaddleOCR-VL OpenAI-compatible endpoint; local mode can use in-process
PaddleOCR. `image_pipeline.py` persists every derived image and sends the
combined OCR/native text to `text_pipeline.py`.
