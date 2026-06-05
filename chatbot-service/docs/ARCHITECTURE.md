# Architecture

## Goal

Build a local Django chatbot that stores conversations, retrieves knowledge from ingested multimodal documents, maintains user-specific memory, and compacts long chats so the LLM context stays useful.

The system should replace the JavaScript/Prisma/Node shape from the reference schema with a Python-only Django design.

## Constraints

- Backend: Django.
- Relational database: PostgreSQL.
- Vector store: Milvus.
- LLM access: LiteLLM.
- Chat/vision model: Gemma 4 26B A4B IT.
- Text embeddings: nvidia/llama-embed-nemotron-8b (dim: 4096).
- Multimodal embeddings: nvidia/nemotron-colembed-vl-8b-v2 (dim: 4096).
- Reranker: Qwen3-VL-Reranker-8B.
- Auth: email/password only.
- Frontend: Django templates and forms.
- Streaming responses are required.
- Tool calls are a first-class part of the execution model and database schema.
- Uploaded knowledge is private per user by default.
- Public share links are allowed through unguessable tokens and revocation.
- Background workers are allowed.
- Local runtime should use Docker Compose for Django, PostgreSQL, Milvus, and model-facing services.
- Use `uv` only for Python dependency installation and command execution.
- No npm, no JavaScript package manager, no Node API service.
- Prefer older, pinned dependency versions according to `pyproject.toml` policy.

## High-Level Components

```text
Browser
  |
  | HTML forms + plain EventSource/fetch streaming where needed
  v
Django web container
  |
  |-- accounts: email/password auth and security logs
  |-- chat: chats, messages, streaming, votes, shares, token usage
  |-- memory: user memories, memory settings, context assembly
  |-- knowledge: documents, assets, chunks, retrieval
  |-- ingestion: file parsing, OCR, image analysis, indexing jobs
  |-- llm: LiteLLM client, model routing, token accounting, Milvus store
  |-- tools: tool registry, permissions, execution, audit trail
  |-- compaction: chat summarization and context compression
  |
  v
PostgreSQL + Milvus
  |
  | PostgreSQL: relational state (users, chats, messages, tool calls, etc.)
  | Milvus: vector search for document chunks and memory embeddings
  v
Python worker container(s)
  |
  | ingestion, compaction, memory extraction, tool jobs
  v
LiteLLM / local model runtime
  |
  | OpenAI-compatible API or LiteLLM provider adapter
  v
Gemma 4 26B A4B IT chat/vision + NVIDIA embedding models + Qwen reranker
```

## Request Flow

### Chat Completion

1. User submits a message from a Django form.
2. Django stores the user message.
3. Context builder loads:
   - recent chat messages,
   - latest chat compaction,
   - user memories,
   - relevant RAG chunks,
   - uploaded attachments linked to the current message.
4. Tool policy selects available tools for the user, chat, and current prompt.
5. LiteLLM calls Gemma 4 (via local GGUF server) in streaming mode.
6. Streaming deltas are written to `chat.MessageDelta` and sent to the browser.
7. If the model requests a tool call, Django records `tools.ToolCall`, validates permission, runs the tool, records `tools.ToolResult`, and resumes generation with the tool result.
8. The final assistant response is stored as `chat.Message`.
9. Token usage, retrieval metadata, stream events, and tool calls are stored.

### Streaming

Streaming is required even with the no-npm constraint.

Recommended implementation:

- server creates a pending assistant message,
- server opens a `StreamingHttpResponse` or SSE endpoint,
- each model delta is persisted as a `MessageDelta`,
- browser displays text incrementally using a small inline script or plain `EventSource`,
- final message content is assembled and committed when the stream completes,
- failed streams keep partial content and status metadata for debugging.

No npm package is needed for this. Browser-native `EventSource` is enough for SSE.

### Tool Calling

Tool calls are central to the system and must be traceable.

Tool flow:

1. Tool registry exposes tool schemas to the LLM.
2. Context builder includes only tools allowed for the current user and chat.
3. Model emits a tool call request.
4. System creates a `ToolCall` row with raw arguments and status `requested`.
5. Permission checks and argument validation run before execution.
6. Tool executor creates a `ToolExecution` attempt row.
7. Tool result is stored in `ToolResult`, including structured output, text output, errors, and timing.
8. Tool result is appended to the model conversation as a tool message.
9. Assistant generation resumes.
10. Final answer references tool results only when relevant.

Initial built-in tools:

- `rag.search`: retrieve private/shared/global knowledge chunks.
- `knowledge.ingest_status`: inspect document ingestion state.
- `memory.search`: retrieve user memories.
- `memory.save`: save or update memory when policy allows it.
- `chat.compact`: compact older chat history.
- `document.analyze`: analyze an uploaded file or asset.

### RAG Search

1. User/admin uploads files or creates a knowledge source.
2. Ingestion job extracts text and metadata.
3. Multimodal analyzer processes images or scanned pages when available.
4. Chunker creates normalized text chunks.
5. Embedding generator creates vectors (text: llama-embed-nemotron-8b, multimodal: nemotron-colembed-vl-8b-v2).
6. Chunks are stored in Milvus with their vectors and metadata.
7. Queries embed the user question and rank chunks by vector similarity in Milvus.
8. Reranker (Qwen3-VL-Reranker-8B) optionally re-ranks top results.
9. Full text search is used as fallback or hybrid boost.

### Chat Compaction

1. When a chat exceeds a token threshold, older messages are summarized.
2. Summary is stored as a versioned compaction record.
3. Compacted messages remain in the database but are not all sent to the LLM.
4. Context builder combines:
   - compacted summary,
   - recent turns,
   - memories,
   - RAG evidence.

## Application Boundaries

### `accounts`

Owns custom user model, email login, password reset, sessions, and security logs.

OAuth, MFA, WebAuthn, WhatsApp, and third-party account tables from the reference schema are intentionally excluded from the MVP.

### `chat`

Owns chats, messages, attachments, votes, shares, stream IDs if needed later, and token usage.

### `memory`

Owns persistent facts/preferences about a user and rules for when memory is retrieved or updated.
Memory embeddings are stored in Milvus.

### `knowledge`

Owns canonical ingested content, document chunks, source metadata, and retrieval result records.
Chunk embeddings are stored in Milvus.

### `ingestion`

Owns file processing jobs and parsers. This app should be operationally isolated because PDF parsing, OCR, and image analysis can be slow.

### `llm`

Owns LiteLLM wrapper code, prompt assembly helpers, model policy, retries, token accounting, and Milvus vector store client.

### `compaction`

Owns summarization of long chats. This can be a separate app or a module inside `chat`; separate app is cleaner if compaction will grow.

## Storage Strategy

- Relational state lives in PostgreSQL.
- Embeddings live in Milvus (separate collections for document chunks and memories).
- Raw uploaded files live in Django storage, initially local filesystem.
- Extracted text and model analysis live in database text/JSON fields.
- All generated model outputs should be traceable through token usage and metadata rows.

## UI Strategy

The MVP should use:

- Django templates.
- `FormView`, `DetailView`, and function/class-based views.
- POST/redirect/GET for chat submissions where practical.
- browser-native streaming for chat responses.
- Plain HTML file upload forms.
- Django admin for operational screens.

No npm, bundler, or JavaScript package is allowed. A small amount of hand-written browser JavaScript is acceptable only where required for streaming and progressive rendering.
