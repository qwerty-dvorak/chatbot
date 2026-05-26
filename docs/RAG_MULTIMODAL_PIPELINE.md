# RAG And Multimodal Pipeline

## Objectives

The RAG system should answer from stored evidence, not invented context. It should support text files, PDFs, images, CSV files, and extracted document assets while keeping each answer traceable to stored chunks and tool calls.

## Supported Inputs

Initial MVP:

- Plain text
- Markdown
- PDF
- Images: PNG, JPEG, WEBP
- CSV as text/table content

Later:

- Office documents
- Audio transcription
- Video frame extraction
- HTML/site dumps

## Ingestion Flow

```text
Upload or local path
  |
  v
Document row created
  |
  v
IngestionJob queued
  |
  v
Extractor picks parser by MIME type
  |
  |-- text extraction
  |-- PDF page extraction
  |-- image OCR / vision analysis
  |-- metadata extraction
  v
Normalized document text
  |
  v
Chunking
  |
  v
Embedding generation (llama-embed-nemotron-8b for text,
  | nemotron-colembed-vl-8b-v2 for multimodal)
  v
Milvus collection: document_chunks
  | (vectors + metadata stored)
  v
Document marked ready
```

## Multimodal Analysis

### Images

For image files and images extracted from PDFs:

- Store the original asset.
- Generate OCR text when available.
- Generate a short visual description.
- Extract visible labels, diagrams, tables, and important entities.
- Store result in `DocumentAsset.analysis`.
- Convert useful analysis into retrievable text chunks.
- Record model/tool metadata for the analysis call.

### PDFs

For PDFs:

- Extract text per page.
- Extract images per page when needed.
- Preserve page number metadata.
- Chunk with page references.
- Store source citations as document title plus page number.

### Tables

For CSV or extracted tables:

- Preserve headers.
- Convert rows into compact text blocks.
- Store original structured metadata where possible.
- Prefer smaller chunks for large tables.

## Chunking Policy

Recommended defaults:

- Target chunk size: 500 to 900 tokens.
- Overlap: 80 to 150 tokens.
- Keep page/section boundaries when possible.
- Store document title and page number in chunk metadata.
- Do not mix unrelated assets into one chunk.

Chunk metadata should include:

- `document_id`
- `source_id`
- `page_number`
- `section_title`
- `asset_id`
- `mime_type`
- `chunk_index`

## Embeddings

Embeddings should be generated through a single wrapper in `apps/llm/embeddings.py`.

Models:

- **Text embedding**: `nvidia/llama-embed-nemotron-8b` (dim: 4096).
- **Multimodal embedding**: `nvidia/nemotron-colembed-vl-8b-v2` (dim: 4096, late-interaction ColBERT-style).
- **Reranker**: `Qwen3-VL-Reranker-8B` (cross-encoder, score output).

Vector dimensions:

- `TEXT_EMBEDDING_DIM` = 4096
- `MULTIMODAL_EMBEDDING_DIM` = 4096

If the embedding model changes dimension, create new Milvus collections or reindex.

## Vector Store: Milvus

Milvus replaces pgvector for all vector storage and search.

Collections:

| Collection | Dimension | Purpose |
|-----------|-----------|---------|
| `document_chunks` | 4096 | Document chunk embeddings for RAG |
| `user_memories` | 4096 | User memory embeddings |

Each collection stores:

- `id` (string): UUID reference to the Django model
- `vector` (float[]): embedding vector
- Additional metadata fields as needed

Milvus is accessed through `apps/llm/milvus_store.py`, which provides:

- `ensure_collections()` - create collections if not exist
- `insert_vectors()` - bulk insert
- `search_vectors()` - vector similarity search
- `delete_vectors()` - delete by IDs
- `delete_by_expr()` - delete by filter expression

## Retrieval Strategy

### Default Hybrid Retrieval

1. Embed the user query with the text embedding model.
2. Run vector similarity search against Milvus `document_chunks` collection.
3. Optionally rerank top results with Qwen3-VL-Reranker-8B.
4. Apply access filtering:
   - user private documents,
   - shared sources,
   - global sources.
5. Return top chunks with citations.

If retrieval is invoked by the LLM as a tool, the full call must be recorded as:

- `tools.ToolCall` for the request,
- `tools.ToolExecution` for the attempt,
- `knowledge.RetrievalRun` for the search,
- `knowledge.RetrievalHit` for selected chunks,
- `tools.ToolResult` for the content returned to the model.

### Fallback

If vector retrieval returns poor results:

- lower similarity threshold once,
- run text-only search,
- ask the model to generate a refined search query,
- retry retrieval,
- store attempts in `RetrievalRun.metadata`.

## Context Assembly

Each LLM call should receive context in this order:

1. System prompt.
2. User profile and memory summary.
3. Latest chat compaction summary.
4. Recent chat turns.
5. RAG evidence with citations.
6. Available tool schemas.
7. Current user message.

The context builder should enforce a token budget:

- reserve output tokens first,
- reserve recent messages second,
- include high-importance memories,
- include best RAG chunks,
- include only tools allowed for this user/session,
- trim lower-scoring evidence if needed.

## Tool Calling In The RAG Loop

Tool calls are not optional glue code; they are part of the agentic RAG loop.

Recommended flow:

1. User sends a message.
2. Context builder includes allowed tools.
3. Model decides whether to answer directly or call a tool.
4. If it calls `rag.search`, the system validates arguments and applies user access filters.
5. Search results are returned as a tool result with compact citations.
6. Model may call another tool, ask for a refined search, save memory, or generate the final answer.
7. Every tool call and result is stored before it is sent back to the model.

Rules:

- Tool calls must be idempotent where possible.
- Tool arguments must be validated against the registered schema.
- Private user knowledge must never leak through tool results.
- Failed tool calls should be returned to the model in a controlled form, not as raw tracebacks.
- Tool outputs should be short enough to fit the active context budget.

## User Memory

Memory should store durable facts, preferences, and recurring instructions, not every message.

Examples worth saving:

- "User prefers concise technical explanations."
- "User is building a Django local RAG chatbot."
- "User prefers no npm or JavaScript package dependencies."

Examples not worth saving:

- One-off troubleshooting logs.
- Temporary file paths.
- Sensitive secrets.
- Full documents.

Auto-save policy:

1. After assistant response, inspect the last exchange.
2. Decide whether it contains stable user preference or project context.
3. If yes, create or update `Memory`.
4. Embed the memory and insert into Milvus for future semantic retrieval.

Memory can also be updated through `memory.save` tool calls, but the tool must respect user settings and never save secrets.

## Chat Compaction

Compaction should start when a chat exceeds a configured threshold, for example:

- more than 40 messages,
- or estimated context above 60 percent of the model budget.

Compaction output should include:

- concise summary,
- durable facts,
- decisions made,
- unresolved questions,
- references to message range compacted.

Compaction must not delete messages. It only creates a summary record used by context assembly.

## Answer Requirements

For RAG answers:

- Prefer grounded answers using retrieved chunks.
- Include source references in the stored metadata.
- Include tool result references in stored metadata when a tool shaped the answer.
- If evidence is weak, say that the knowledge base does not contain enough support.
- Do not fabricate usernames, dates, filenames, page numbers, or scores.
- Store retrieval hits for debugging.

## Background Processing

Background workers are allowed and should be Python-only:

- Django management command loop,
- cron/systemd timer,
- Celery/RQ only if dependency policy allows it.
- Docker Compose worker services for ingestion, compaction, and scheduled maintenance.

No Node worker should be introduced.
