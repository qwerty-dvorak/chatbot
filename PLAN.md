# Chat and RAG Architecture Revamp

  ## Summary

  Replace the current visibility/tier-coupled RAG integration with:

  - ACL-controlled knowledge and chat sharing.
  - Deduplicated content and embedding artifacts.
  - One full ingestion pipeline without tiers or promotion.
  - Durable PostgreSQL-backed ingestion and chat-turn workers.
  - LLM-based document selection followed by filtered hybrid retrieval.
  - Persistent traces for model calls, BM25, vector search, reranking, OCR, and other expensive operations.
  - Reconnectable frontend progress, attachment management, branching, and timing views.
  - A destructive clean reset with one fresh initial migration per Django app.

  Remove the legacy local RAG fallback; chatbot-service will use the standalone RAG API whenever RAG is enabled.

  ## Schema and Domain Changes

  - Separate immutable storage from user-visible documents:
      - ContentBlob: content hash, MIME type, size, object key, storage status.
      - ArtifactRevision: blob, pipeline fingerprint/version, extracted text, summary, processing status.
      - DocumentReference: owner, artifact revision, title, kind (knowledge or chat_attachment), timestamps.
      - DocumentGrant: named user and viewer/editor role for Knowledge documents.
      - DocumentChunk, DocumentAsset, and EmbeddingSet belong to an artifact revision, allowing reuse across references.

  - Deduplicate using sha256 + pipeline fingerprint + model/config versions. Concurrent ingestion must lock or atomically upsert this key.
  - Chat attachments:
      - Replace Message.attachments JSON with relational MessageAttachment.
      - Link each attachment to a chat-scoped DocumentReference.
      - Inherit access from the chat and remain outside Knowledge until “Add to Knowledge” creates another reference to the same artifact.
      - Remove the chat reference when the chat is deleted; garbage-collect blobs/artifacts only when no references remain.

  - Chat collaboration and branching:
      - Add ChatGrant with named-user viewer and editor roles.
      - Add Message.author.
      - Add ChatBranch with base/head messages; normal turns serialize per branch.
      - “Branch from here” creates a branch without copying messages; context follows the selected message-parent chain.
      - Keep revocable public links read-only. They expose transcript and attachment downloads, but not traces, RAG diagnostics, ACLs, or Knowledge documents.

  - Durable execution:
      - TurnRun represents an accepted chat turn and its queued/running/terminal state.
      - TurnEvent stores ordered progress, text deltas, tool events, and terminal events for SSE replay.
      - IngestionJob and IngestionStepAttempt move the queue/progress store from SQLite into PostgreSQL.
      - Workers claim queued rows with SELECT … FOR UPDATE SKIP LOCKED.

  - Observability:
      - Replace fragmented timing JSON and TokenUsage records with ExecutionTrace, nested ExecutionSpan, and model-call fields/extensions.
      - Persist operation, provider, model, LoRA, duration, time-to-first-token where available, input/output/total tokens, retry count, status, error code, and sanitized dimensions/counts.
      - Do not persist prompts, responses, retrieved text, or secrets.
      - Replace overlapping RagSearchLog/retrieval models with normalized RetrievalRun, DocumentSelection, and RetrievalHit records.

  ## RAG and Application Flow

  - Remove private/shared/global visibility, instant/slow/global tiers, promotion APIs, and all mappings between access and processing.
  - Every file runs one complete content-aware pipeline: extraction, derived assets, OCR when applicable, chunking, summary generation, hypothetical questions, text/multimodal embedding, and indexing. Non-applicable steps are recorded
    as skipped.

  - Replace the pickle-based rank-bm25 index with Milvus-native sparse BM25 and hybrid dense+sparse retrieval. Pin compatible Milvus server/client versions instead of using latest. Milvus supports native BM25 fields and filtered hybrid
    retrieval. Milvus BM25 documentation (https://milvus.io/docs/bm25-function.md)

  - Store scalar artifact revision IDs, chunk IDs/types, and model/version identifiers in Milvus. PostgreSQL remains authoritative for ownership and ACLs.
  - Retrieval for every turn:
      1. Resolve all Knowledge documents accessible to the user plus attachments on the selected chat branch.
      2. Search summary vectors, batching Milvus artifact-ID filters as needed for the 10,000-document target.
      3. Pass at most 20 candidates—ID, title, summary, MIME type, scope, and safe metadata—to the active chat model/LoRA.
      4. Persist its selected document IDs and rationale metadata; allow at most eight selected documents.
      5. Run filtered dense+BM25 chunk retrieval, fusion, hypothetical-question resolution, and reranking only inside those artifacts.
      6. Inject the final cited chunks into chat context.
      7. If selection fails, fall back to the top three summary candidates; if the model selects none, inject no document context.

  - Attachment turns wait for all files to finish ingestion. Proceed when at least one succeeds and surface individual failures; if all fail, terminate the turn without calling the chat model.
  - API changes:
      - POST /v1/ingestions: multipart file plus document-reference ID; no tier/profile fields.
      - GET /v1/ingestions/{id}: durable job, step attempts, and trace summary.
      - POST /v1/routing-candidates: query plus authorized artifact IDs.
      - POST /v1/search: query plus selector-approved artifact IDs and retrieval limits.
      - Remove /v1/promote and tier parameters.

  - The RAG API remains unauthenticated by explicit decision and must only bind to a firewall-restricted private network. Health endpoints may be exposed; retrieval and ingestion endpoints must not be publicly routed.

  ## Frontend and Worker Behavior

   - Message POST creates messages and a TurnRun, returning immediately. It never performs ingestion or generation in the request.
  - Dedicated turn workers serialize normal turns per branch and persist all output/events. RAG workers process PostgreSQL ingestion jobs independently.
  - SSE becomes read-only and replayable using a run ID and last sequence; reconnecting or refreshing never starts duplicate work.
  - Add a right-side attachment drawer:
      - Small image/PDF-page previews where available.
      - Filename, type, size, uploader, branch/message, pipeline status, and current step.
      - Download, preview, and owner/editor “Add to Knowledge” actions.
      - Responsive drawer behavior on narrow screens.

  - Show live file-step progress before generation and model/retrieval progress afterward.
  - Add compact elapsed time and token totals to authenticated chat turns, with an expandable trace waterfall for model selection, embeddings, BM25, vector search, fusion, reranking, context assembly, tool calls, and chat generation.
  - Add a branch selector and “Branch from here” message action.
  - Named editors may append messages and attachments; viewers are read-only. Owners alone manage grants, public links, and deletion.

  ## Migration, Reset, Documentation, and Tests

  - Finalize models first, delete all existing application migration files, then generate one 0001_initial.py per installed Django app. Django remains the sole owner of the shared PostgreSQL schema.
  - Add one confirmed reset command that stops services and clears PostgreSQL, Milvus, ingestion jobs, BM25 remnants, object storage, uploads, and derived assets before applying fresh migrations and recreating collections.
  - Update launch scripts to start the web process, turn worker, RAG API, and one or more RAG workers without Docker Compose.
  - Update architecture documentation and add a glossary defining Content Blob, Artifact Revision, Document Reference, Chat Attachment, Knowledge Document, Grant, Turn Run, Trace, and Span. Record ADRs for artifact/reference separation,
    PostgreSQL orchestration, and Milvus-native hybrid retrieval.

  - Test:
      - Fresh migration from an empty database and makemigrations --check.
      - ACL isolation for owners, viewers, editors, public links, downloads, and RAG filters.
      - Concurrent deduplication and reference-counted cleanup.
      - Complete text/PDF/image ingestion, progress replay, retries, partial failure, and all-failed behavior.
      - Real selector, embedding, OCR, reranker, and chat model calls with timing/token/LoRA assertions.
      - Dense+BM25 hybrid retrieval and batched authorization filters.
      - SSE reconnect without duplicate execution.
      - Per-branch serialization, explicit forks, and branch-specific context.
      - Knowledge promotion without reprocessing.
      - Full destructive reset and clean-stack integration through tests/run.sh all.

  ## Assumptions

  - This is a development reset: no data migration, compatibility bridge, or rollback is required.
  - ACL grants target individual users only in v1.
  - The target is at most 10,000 accessible documents per user.
  - Chat attachments remain chat-scoped until deletion or explicit promotion.
  - Public links expose transcript and attachments only.
