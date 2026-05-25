# Data Model

This model plan translates the provided Prisma-style schema into Django/PostgreSQL tables and adds RAG-specific entities.

The MVP should keep only the pieces needed for:

- email/password auth,
- chat history,
- sharing/votes,
- user memory,
- RAG ingestion and retrieval,
- tool call execution and audit trail,
- streaming event persistence,
- token usage,
- security logging.

## Keep From Reference Schema

| Reference model | Django target | Notes |
| --- | --- | --- |
| `User` | `accounts.User` | Custom user using email as login identifier. |
| `Session` | Django sessions | Use built-in Django session table unless custom session metadata is needed. |
| `Chat` | `chat.Chat` | Keep archive and ordering indexes. |
| `Message` | `chat.Message` | Keep role/content/attachments/tool metadata. Tool calls are normalized into dedicated tables. |
| `Vote` | `chat.Vote` | Keep composite uniqueness by chat/message/user if per-user votes are needed. |
| `ChatShare` | `chat.ChatShare` | Useful for share links. |
| `SecurityLog` | `accounts.SecurityLog` | Keep for login and sensitive events. |
| `Memory` | `memory.Memory` | Add optional embedding for semantic retrieval. |
| `MemorySettings` | `memory.MemorySettings` | Keep auto-save and token budget settings. |
| `StreamId` | `chat.StreamId` | Optional. Keep only if resumable generations are required. |
| `TokenUsage` | `llm.TokenUsage` | Used across chat, ingestion, tool calls, memory, and compaction. |
| `CanvasDocument` | `chat.CanvasDocument` or remove | Keep only if editable generated documents are required. |

## Exclude From MVP

These reference models should not be built initially because auth is email/password only or they are domain-specific:

- `Account`
- `VerificationToken`, unless custom email verification is required
- `WebAuthnCredential`
- MFA fields on `User`
- `WhatsAppConversation`
- `WhatsAppMessage`
- `VTOPSnapshot`
- `Broadcast`
- `Migration`, because Django manages migrations
- CockroachDB region enum

## Core Models

### `accounts.User`

Purpose: email/password identity and user preferences.

Important fields:

- `id`: UUID primary key.
- `email`: unique, indexed.
- `name`: optional display name.
- `preferences`: JSON.
- `is_active`, `is_staff`, `is_superuser`: Django auth fields.
- `created_at`, `updated_at`.

Recommended design:

- Extend `AbstractBaseUser` + `PermissionsMixin`, or carefully customize `AbstractUser`.
- Set `USERNAME_FIELD = "email"`.
- Do not require username.

### `accounts.SecurityLog`

Purpose: audit login and sensitive actions.

Fields:

- `id`
- `user`
- `event`
- `method`
- `ip_address`
- `user_agent`
- `metadata`
- `created_at`

Indexes:

- `(user, -created_at)`
- `(event, -created_at)`

### `chat.Chat`

Purpose: conversation container.

Fields:

- `id`
- `user`
- `title`
- `path`
- `archived`
- `created_at`
- `updated_at`

Indexes:

- `(user, archived, -updated_at)`
- `(user, -updated_at)`
- `archived`

### `chat.Message`

Purpose: immutable chat message record.

Fields:

- `id`
- `chat`
- `role`: `system`, `user`, `assistant`, `tool`
- `content`
- `tool_invocations`: JSON
- `attachments`: JSON
- `metadata`: JSON
- `status`: `pending`, `streaming`, `completed`, `failed`, `cancelled`
- `parent_message`: nullable self-reference for threaded/tool flows
- `created_at`
- `completed_at`

Indexes:

- `(chat, created_at)`
- `(chat, role, -created_at)`
- `(chat, status, -created_at)`

### `chat.MessageDelta`

Purpose: durable record of streamed response chunks.

Fields:

- `id`
- `message`
- `sequence`
- `delta_type`: `text`, `tool_call`, `tool_result`, `error`, `done`
- `content`
- `raw_event`
- `created_at`

Constraints:

- unique `(message, sequence)`

Indexes:

- `(message, sequence)`

### `chat.MessageAttachment`

Purpose: normalized attachment storage for uploads connected to a message.

Fields:

- `id`
- `message`
- `file`
- `mime_type`
- `size_bytes`
- `sha256`
- `analysis_status`
- `analysis_text`
- `analysis_metadata`
- `created_at`

This is preferred over storing all attachment state only in `Message.attachments`.

### `chat.ChatShare`

Purpose: shareable read-only chat link.

Fields:

- `id`
- `chat`
- `user`
- `title`
- `revoked`
- `created_at`

Indexes:

- `(chat, revoked)`
- `(user, -created_at)`

### `chat.Vote`

Purpose: user feedback on assistant messages.

Fields:

- `id`
- `chat`
- `message`
- `user`
- `is_upvoted`
- `created_at`

Constraints:

- unique `(user, message)`

### `llm.TokenUsage`

Purpose: token accounting for chat, retrieval, ingestion analysis, and compaction.

Fields:

- `id`
- `user`
- `chat`
- `message`
- `operation`: `chat`, `embedding`, `vision_analysis`, `compaction`, `memory`, `tool_call`
- `model`
- `step_index`
- `provider`
- `request_id`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `metadata`
- `created_at`

Indexes:

- `created_at`
- `(user, -created_at)`
- `(chat, -created_at)`
- `model`
- `operation`

## Memory Models

### `memory.Memory`

Purpose: durable user-specific facts, preferences, and recurring context.

Fields:

- `id`
- `user`
- `content`
- `tags`: PostgreSQL array or JSON list
- `importance`
- `embedding`: vector, optional but recommended
- `last_used_at`
- `created_at`
- `updated_at`

Indexes:

- `(user, importance, -last_used_at)`
- `(user, -updated_at)`
- vector index on `embedding`

### `memory.MemorySettings`

Purpose: per-user memory policy.

Fields:

- `id`
- `user`: one-to-one
- `is_enabled`
- `max_tokens`
- `auto_save`
- `auto_save_filter`: `low`, `medium`, `high`
- `allow_tool_updates`
- `created_at`
- `updated_at`

Default:

- `is_enabled = true`
- `auto_save = true`
- user can customize all memory settings

## Knowledge And RAG Models

### `knowledge.KnowledgeSource`

Purpose: logical source such as an uploaded folder, manual upload, website dump, or admin corpus.

Fields:

- `id`
- `owner`: nullable user
- `name`
- `source_type`: `upload`, `folder`, `manual`, `api`
- `visibility`: `private`, `shared`, `global`
- `metadata`
- `created_at`
- `updated_at`

Indexes:

- `(owner, source_type)`
- `visibility`

Default:

- user uploads use `visibility = private`
- admin-created sources may be `global`

### `knowledge.Document`

Purpose: canonical ingested document.

Fields:

- `id`
- `source`
- `owner`: nullable user
- `title`
- `original_filename`
- `mime_type`
- `file`
- `sha256`
- `status`: `pending`, `processing`, `ready`, `failed`
- `extracted_text`
- `analysis_summary`
- `metadata`
- `created_at`
- `updated_at`

Indexes:

- `(owner, -created_at)`
- `(source, status)`
- `sha256`

### `knowledge.DocumentAsset`

Purpose: extracted images/pages/tables from a document.

Fields:

- `id`
- `document`
- `asset_type`: `image`, `page`, `table`, `audio`, `video_frame`
- `file`
- `mime_type`
- `page_number`
- `text`
- `analysis`
- `metadata`
- `created_at`

Indexes:

- `(document, asset_type)`
- `(document, page_number)`

### `knowledge.DocumentChunk`

Purpose: retrievable text unit.

Fields:

- `id`
- `document`
- `asset`: nullable
- `chunk_index`
- `content`
- `content_hash`
- `token_count`
- `embedding`
- `metadata`
- `created_at`

Indexes:

- unique `(document, chunk_index)`
- `(document, chunk_index)`
- PostgreSQL full text index on `content`
- `pgvector` index on `embedding`

Important: `embedding` dimension must match `nvidia/llama-embed-nemotron-8b` as exposed by the local embedding runtime. Confirm the exact output dimension before the first migration because pgvector dimensions are schema-level decisions.

### `ingestion.IngestionJob`

Purpose: track async or batch processing.

Fields:

- `id`
- `document`
- `status`: `queued`, `running`, `succeeded`, `failed`
- `attempts`
- `error`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

Indexes:

- `(status, created_at)`
- `(document, -created_at)`

### `knowledge.RetrievalRun`

Purpose: trace what retrieval did for a chat turn.

Fields:

- `id`
- `user`
- `chat`
- `message`
- `query`
- `query_embedding`
- `strategy`: `vector`, `text`, `hybrid`
- `tool_call`: nullable reference to a tool call when retrieval was invoked by `rag.search`
- `metadata`
- `created_at`

### `knowledge.RetrievalHit`

Purpose: retrieved chunk evidence.

Fields:

- `id`
- `run`
- `chunk`
- `rank`
- `score`
- `source_title`
- `source_metadata`
- `created_at`

Constraints:

- unique `(run, chunk)`

## Tool Models

Tool calls are very important to the entire flow. Do not keep them only inside message JSON. JSON on `Message` may store the provider payload for convenience, but normalized records are required for traceability, retries, permissions, debugging, and analytics.

### `tools.ToolDefinition`

Purpose: registry entry for a callable tool.

Fields:

- `id`
- `name`: unique, for example `rag.search`
- `display_name`
- `description`
- `version`
- `schema`: JSON schema for arguments
- `result_schema`: optional JSON schema for result
- `is_enabled`
- `is_builtin`
- `requires_confirmation`
- `permission_level`: `user`, `staff`, `admin`, `system`
- `timeout_seconds`
- `metadata`
- `created_at`
- `updated_at`

Indexes:

- `name`
- `(is_enabled, permission_level)`

### `tools.ToolCall`

Purpose: model-requested tool call for a chat turn.

Fields:

- `id`
- `tool_call_id`: provider-supplied ID, nullable
- `user`
- `chat`
- `message`: assistant message that requested the tool
- `tool`
- `name_snapshot`
- `version_snapshot`
- `status`: `requested`, `validated`, `running`, `succeeded`, `failed`, `denied`, `cancelled`, `timed_out`
- `arguments`: validated JSON arguments
- `raw_arguments`: raw provider arguments before validation
- `validation_errors`
- `permission_result`: JSON explaining allow/deny decision
- `sequence`
- `started_at`
- `completed_at`
- `created_at`
- `updated_at`

Indexes:

- `(chat, sequence)`
- `(user, -created_at)`
- `(tool, status, -created_at)`
- `(message, sequence)`
- `status`

Constraints:

- unique `(message, sequence)`

### `tools.ToolExecution`

Purpose: individual execution attempt for a tool call.

Fields:

- `id`
- `tool_call`
- `attempt`
- `worker_id`
- `status`: `running`, `succeeded`, `failed`, `timed_out`, `cancelled`
- `input_snapshot`
- `stdout`
- `stderr`
- `error_type`
- `error_message`
- `duration_ms`
- `started_at`
- `completed_at`
- `created_at`

Indexes:

- `(tool_call, attempt)`
- `(status, -created_at)`

Constraints:

- unique `(tool_call, attempt)`

### `tools.ToolResult`

Purpose: durable result returned to the model.

Fields:

- `id`
- `tool_call`
- `execution`
- `content`: text result sent back as tool message
- `structured_content`: JSON result for application use
- `artifact`: optional file output
- `metadata`
- `created_at`

Indexes:

- `tool_call`
- `(created_at)`

### `tools.ToolPermissionGrant`

Purpose: optional per-user or per-role permission override.

Fields:

- `id`
- `tool`
- `user`: nullable
- `role`: nullable string such as `staff`
- `is_allowed`
- `constraints`: JSON, for example source visibility or max rows
- `created_at`
- `updated_at`

Indexes:

- `(tool, user)`
- `(tool, role)`

Initial tool definitions:

- `rag.search`
- `knowledge.ingest_status`
- `memory.search`
- `memory.save`
- `chat.compact`
- `document.analyze`

## Compaction Models

### `compaction.ChatCompaction`

Purpose: versioned summary of older chat turns.

Fields:

- `id`
- `chat`
- `from_message`
- `to_message`
- `summary`
- `facts`
- `open_questions`
- `token_count`
- `model`
- `created_at`

Indexes:

- `(chat, -created_at)`
- `(chat, from_message, to_message)`

## Recommended Deletion Behavior

- Deleting a user deletes private chats, memories, and private knowledge sources.
- Global knowledge sources should not be deleted when an admin user is removed.
- Deleting a document deletes assets, chunks, and related ingestion jobs.
- Deleting a chat deletes messages, votes, compactions, retrieval runs, tool calls, stream deltas, and chat token usage.
