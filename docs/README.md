# Chatbot RAG System Documentation

This folder defines the planned architecture and file structure for a local Django chatbot with:

- Email/password authentication
- Conversation history storage
- User-level memory and context
- Chat compaction for long-running conversations
- Built-in RAG over user/admin ingested content
- Multimodal ingestion and analysis
- PostgreSQL for relational data
- Milvus vector store for embeddings
- LiteLLM connected to local model endpoints
- First-class tool calling with durable call/result records
- Streaming responses
- Docker Compose local runtime
- `uv` only for Python dependency and command execution
- No npm, no JavaScript package dependency, and no Node service

These are planning documents only. They intentionally do not add application code.

## Documents

- [Architecture](./ARCHITECTURE.md): system boundaries, app responsibilities, and data flow.
- [File Structure](./FILE_STRUCTURE.md): proposed Django project layout.
- [Data Model](./DATA_MODEL.md): Django model plan mapped from the provided Prisma-style schema.
- [Detailed DB Schema](./DETAILED_DB_SCHEMA.md): concrete table, field, index, and constraint plan.
- [RAG And Multimodal Pipeline](./RAG_MULTIMODAL_PIPELINE.md): ingestion, chunking, retrieval, memory, and compaction flow.
- [API And Views](./API_AND_VIEWS.md): Django routes, views, forms, and non-JS UX approach.
- [Configuration And Operations](./CONFIGURATION_AND_OPERATIONS.md): dependencies, environment variables, tasks, and deployment notes.
- [Open Questions](./OPEN_QUESTIONS.md): decisions that should be confirmed before implementation.

## Baseline Assumptions

- The current `pyproject.toml` is the version policy reference.
- Dependency versions should be pinned deliberately before implementation.
- `uv` is the only Python dependency and command runner.
- The application is local-first and should not require cloud inference.
- Chat and multimodal reasoning model: `Gemma 4 26B A4B IT`, 256K context window.
- Text embedding model: `nvidia/llama-embed-nemotron-8b` (dim: 4096).
- Multimodal embedding model: `nvidia/nemotron-colembed-vl-8b-v2` (dim: 4096).
- Reranker: `Qwen3-VL-Reranker-8B`.
- Vector store: Milvus (two collections: document_chunks, user_memories).
- All models will be reached through LiteLLM using local OpenAI-compatible endpoints.
- The first frontend should be Django server-rendered HTML using forms and standard browser navigation.
- Streaming is required. Implement it with Django `StreamingHttpResponse` or Server-Sent Events using plain browser APIs and no npm packages.
- Uploaded knowledge is private to the uploading user by default.
- Public chat share links are supported through unguessable tokens and revocation.
- Memory is auto-saved by default and user-customizable.
- Background workers are allowed, but must be Python-only and managed through Docker Compose.

## Implementation Order

1. Create Django project and apps.
2. Configure PostgreSQL, Milvus, and custom email auth.
3. Implement chat, messages, votes, shares, token usage, and security logs.
4. Implement tool registry, tool call execution, and durable tool call records.
5. Implement streaming chat generation through LiteLLM.
6. Implement document ingestion models and background processing.
7. Implement embeddings, Milvus collections, chunks, and retrieval.
8. Add user memory and chat compaction.
9. Add multimodal file analysis for text, Markdown, PDF, images, and CSV.
10. Add Docker Compose services, admin screens, operational commands, tests, and fixtures.
