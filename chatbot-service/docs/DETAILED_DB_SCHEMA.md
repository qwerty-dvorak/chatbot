# Detailed DB Schema

This document is the concrete PostgreSQL/Django schema target. Field names use Django style. Database table names should be explicit through `db_table` so migrations stay predictable.

Primary key recommendation: UUID for all application tables unless there is a strong reason to use integer IDs.

Timestamp convention:

- `created_at`: set on insert.
- `updated_at`: set on update.
- use timezone-aware timestamps.

JSON convention:

- use `JSONField(default=dict, blank=True)` for metadata objects.
- use `JSONField(default=list, blank=True)` for list-like values unless PostgreSQL arrays are clearly better.

Vector storage: All vectors are stored in Milvus, not PostgreSQL. The Milvus collections are:

- `document_chunks` (dim: 4096) - document chunk embeddings
- `user_memories` (dim: 4096) - user memory embeddings

## Extensions

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

`pgcrypto` is useful for UUID generation if database-side UUID defaults are used.

## Accounts

### Table: `users`

Custom email/password user.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `email` | varchar(254) | no | none | Unique login identifier |
| `name` | varchar(255) | yes | null | Display name |
| `password` | varchar(128) | no | none | Django password hash |
| `preferences` | jsonb | no | `{}` | User UI/model preferences |
| `is_active` | boolean | no | true | Django auth |
| `is_staff` | boolean | no | false | Django admin |
| `is_superuser` | boolean | no | false | Django permissions |
| `last_login` | timestamptz | yes | null | Django auth |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Constraints:

- primary key `id`
- unique `email`

Indexes:

- `users_email_idx` on `email`
- `users_created_at_idx` on `created_at`

### Table: `security_logs`

Audit trail for auth and sensitive operations.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `user_id` | uuid | yes | null | FK to `users`, null for anonymous failures |
| `event` | varchar(100) | no | none | `login_success`, `login_failed`, etc. |
| `method` | varchar(50) | yes | null | `password`, `admin`, `system` |
| `ip_address` | inet | yes | null | Request IP |
| `user_agent` | text | yes | null | Browser/client |
| `metadata` | jsonb | no | `{}` | Extra audit data |
| `created_at` | timestamptz | no | now | Event time |

Indexes:

- `security_logs_user_created_idx` on `(user_id, created_at DESC)`
- `security_logs_event_created_idx` on `(event, created_at DESC)`

## Chat

### Table: `chats`

Conversation container.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `user_id` | uuid | no | none | FK to `users` |
| `title` | varchar(255) | no | none | Chat title |
| `path` | varchar(512) | no | none | Stable route/path slug |
| `archived` | boolean | no | false | Archive flag |
| `metadata` | jsonb | no | `{}` | Model/profile settings |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Constraints:

- FK `user_id` references `users(id)` on delete cascade
- unique `(user_id, path)`

Indexes:

- `chats_user_archived_updated_idx` on `(user_id, archived, updated_at DESC)`
- `chats_user_updated_idx` on `(user_id, updated_at DESC)`
- `chats_archived_idx` on `archived`

### Table: `messages`

Immutable chat messages. Tool calls are normalized separately.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `chat_id` | uuid | no | none | FK to `chats` |
| `parent_message_id` | uuid | yes | null | Optional self-reference |
| `role` | varchar(30) | no | none | `system`, `user`, `assistant`, `tool` |
| `content` | text | no | empty | Final message text |
| `status` | varchar(30) | no | `completed` | `pending`, `streaming`, `completed`, `failed`, `cancelled` |
| `tool_invocations` | jsonb | no | `{}` | Raw provider payload snapshot only |
| `attachments` | jsonb | no | `[]` | Lightweight attachment summary |
| `metadata` | jsonb | no | `{}` | Generation/retrieval metadata |
| `created_at` | timestamptz | no | now | Created timestamp |
| `completed_at` | timestamptz | yes | null | Completion time |

Constraints:

- FK `chat_id` references `chats(id)` on delete cascade
- FK `parent_message_id` references `messages(id)` on delete set null

Indexes:

- `messages_chat_created_idx` on `(chat_id, created_at)`
- `messages_chat_role_created_idx` on `(chat_id, role, created_at DESC)`
- `messages_chat_status_created_idx` on `(chat_id, status, created_at DESC)`

### Table: `message_deltas`

Persisted streaming chunks.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `message_id` | uuid | no | none | FK to `messages` |
| `sequence` | integer | no | none | Monotonic per message |
| `delta_type` | varchar(30) | no | `text` | `text`, `tool_call`, `tool_result`, `error`, `done` |
| `content` | text | no | empty | Stream text or compact event text |
| `raw_event` | jsonb | no | `{}` | Raw LiteLLM/provider event |
| `created_at` | timestamptz | no | now | Event time |

Constraints:

- FK `message_id` references `messages(id)` on delete cascade
- unique `(message_id, sequence)`

Indexes:

- `message_deltas_message_sequence_idx` on `(message_id, sequence)`

### Table: `message_attachments`

Uploaded files attached to chat messages.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `message_id` | uuid | no | none | FK to `messages` |
| `file` | varchar(1024) | no | none | Django storage path |
| `original_filename` | varchar(255) | no | none | Original upload name |
| `mime_type` | varchar(255) | no | none | Detected MIME |
| `size_bytes` | bigint | no | 0 | File size |
| `sha256` | char(64) | no | none | Content hash |
| `analysis_status` | varchar(30) | no | `pending` | `pending`, `processing`, `ready`, `failed` |
| `analysis_text` | text | no | empty | Extracted/vision text |
| `analysis_metadata` | jsonb | no | `{}` | Tool/model details |
| `created_at` | timestamptz | no | now | Created timestamp |

Indexes:

- `message_attachments_message_idx` on `message_id`
- `message_attachments_sha256_idx` on `sha256`

### Table: `chat_shares`

Public share links.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `chat_id` | uuid | no | none | FK to `chats` |
| `user_id` | uuid | no | none | Owner |
| `token` | varchar(128) | no | generated | Unguessable public token |
| `title` | varchar(255) | yes | null | Optional share title |
| `revoked` | boolean | no | false | Revocation flag |
| `expires_at` | timestamptz | yes | null | Optional expiry |
| `created_at` | timestamptz | no | now | Created timestamp |

Constraints:

- unique `token`
- FK `chat_id` references `chats(id)` on delete cascade
- FK `user_id` references `users(id)` on delete cascade

Indexes:

- `chat_shares_chat_revoked_idx` on `(chat_id, revoked)`
- `chat_shares_user_created_idx` on `(user_id, created_at DESC)`
- `chat_shares_token_idx` on `token`

Public shares should expose stored chat content only. They should not allow public users to rerun tools against the owner's private knowledge base.

### Table: `votes`

Feedback on assistant messages.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `chat_id` | uuid | no | none | FK to `chats` |
| `message_id` | uuid | no | none | FK to `messages` |
| `user_id` | uuid | no | none | FK to `users` |
| `is_upvoted` | boolean | no | none | Vote value |
| `created_at` | timestamptz | no | now | Created timestamp |

Constraints:

- unique `(user_id, message_id)`

Indexes:

- `votes_chat_idx` on `chat_id`
- `votes_message_idx` on `message_id`

## Tool Calling

Tool calls are a core feature. Every model-requested tool call must be recorded separately from message JSON.

### Table: `tool_definitions`

Registry of available tools.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `name` | varchar(120) | no | none | Unique tool name |
| `display_name` | varchar(255) | no | none | Human label |
| `description` | text | no | empty | Model/user description |
| `version` | varchar(50) | no | `1` | Tool schema version |
| `schema` | jsonb | no | `{}` | JSON schema for arguments |
| `result_schema` | jsonb | no | `{}` | Optional result schema |
| `is_enabled` | boolean | no | true | Runtime availability |
| `is_builtin` | boolean | no | true | Built-in vs custom |
| `requires_confirmation` | boolean | no | false | Whether user confirmation is required |
| `permission_level` | varchar(30) | no | `user` | `user`, `staff`, `admin`, `system` |
| `timeout_seconds` | integer | no | 60 | Execution timeout |
| `metadata` | jsonb | no | `{}` | Extra config |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Constraints:

- unique `name`

Indexes:

- `tool_definitions_enabled_permission_idx` on `(is_enabled, permission_level)`

### Table: `tool_calls`

A model-requested tool call within a chat turn.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `tool_call_id` | varchar(255) | yes | null | Provider-supplied ID |
| `user_id` | uuid | no | none | FK to `users` |
| `chat_id` | uuid | no | none | FK to `chats` |
| `message_id` | uuid | no | none | Assistant message requesting tool |
| `tool_id` | uuid | no | none | FK to `tool_definitions` |
| `name_snapshot` | varchar(120) | no | none | Tool name at call time |
| `version_snapshot` | varchar(50) | no | none | Tool version at call time |
| `status` | varchar(30) | no | `requested` | Lifecycle status |
| `arguments` | jsonb | no | `{}` | Validated arguments |
| `raw_arguments` | jsonb | no | `{}` | Provider arguments before validation |
| `validation_errors` | jsonb | no | `[]` | Validation failure details |
| `permission_result` | jsonb | no | `{}` | Allow/deny decision details |
| `sequence` | integer | no | none | Order within assistant message |
| `started_at` | timestamptz | yes | null | Execution start |
| `completed_at` | timestamptz | yes | null | Execution completion |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Status values:

- `requested`
- `validated`
- `running`
- `succeeded`
- `failed`
- `denied`
- `cancelled`
- `timed_out`

Constraints:

- FK `user_id` references `users(id)` on delete cascade
- FK `chat_id` references `chats(id)` on delete cascade
- FK `message_id` references `messages(id)` on delete cascade
- FK `tool_id` references `tool_definitions(id)` on delete restrict
- unique `(message_id, sequence)`

Indexes:

- `tool_calls_chat_sequence_idx` on `(chat_id, sequence)`
- `tool_calls_user_created_idx` on `(user_id, created_at DESC)`
- `tool_calls_tool_status_created_idx` on `(tool_id, status, created_at DESC)`
- `tool_calls_message_sequence_idx` on `(message_id, sequence)`
- `tool_calls_status_idx` on `status`

### Table: `tool_executions`

Execution attempts for a tool call.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `tool_call_id` | uuid | no | none | FK to `tool_calls` |
| `attempt` | integer | no | 1 | Attempt number |
| `worker_id` | varchar(255) | yes | null | Worker/container identity |
| `status` | varchar(30) | no | `running` | Attempt status |
| `input_snapshot` | jsonb | no | `{}` | Arguments plus execution context |
| `stdout` | text | no | empty | Captured output if any |
| `stderr` | text | no | empty | Captured error output if any |
| `error_type` | varchar(255) | yes | null | Exception class/category |
| `error_message` | text | no | empty | Sanitized error |
| `duration_ms` | integer | yes | null | Runtime |
| `started_at` | timestamptz | no | now | Start time |
| `completed_at` | timestamptz | yes | null | Completion time |
| `created_at` | timestamptz | no | now | Created timestamp |

Constraints:

- FK `tool_call_id` references `tool_calls(id)` on delete cascade
- unique `(tool_call_id, attempt)`

Indexes:

- `tool_executions_call_attempt_idx` on `(tool_call_id, attempt)`
- `tool_executions_status_created_idx` on `(status, created_at DESC)`

### Table: `tool_results`

Result returned to the model.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `tool_call_id` | uuid | no | none | FK to `tool_calls` |
| `execution_id` | uuid | yes | null | FK to `tool_executions` |
| `content` | text | no | empty | Text sent back as tool message |
| `structured_content` | jsonb | no | `{}` | App-readable result |
| `artifact` | varchar(1024) | yes | null | Optional output file |
| `metadata` | jsonb | no | `{}` | Timing, citations, redaction info |
| `created_at` | timestamptz | no | now | Created timestamp |

Constraints:

- FK `tool_call_id` references `tool_calls(id)` on delete cascade
- FK `execution_id` references `tool_executions(id)` on delete set null

Indexes:

- `tool_results_call_idx` on `tool_call_id`
- `tool_results_created_idx` on `created_at`

### Table: `tool_permission_grants`

Optional permission overrides.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `tool_id` | uuid | no | none | FK to `tool_definitions` |
| `user_id` | uuid | yes | null | Optional user-specific grant |
| `role` | varchar(50) | yes | null | Optional role grant |
| `is_allowed` | boolean | no | true | Allow or deny |
| `constraints` | jsonb | no | `{}` | Scope limits |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Indexes:

- `tool_permission_grants_tool_user_idx` on `(tool_id, user_id)`
- `tool_permission_grants_tool_role_idx` on `(tool_id, role)`

Initial built-in tool definitions:

| Tool | Purpose | Permission |
| --- | --- | --- |
| `rag.search` | Search accessible knowledge chunks | user |
| `knowledge.ingest_status` | Check document ingestion state | user |
| `memory.search` | Retrieve current user's memories | user |
| `memory.save` | Save/update memory under user settings | user |
| `chat.compact` | Compact current chat | user |
| `document.analyze` | Analyze user-owned uploaded document or asset | user |

## Memory

### Table: `memories`

User-specific durable context.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `user_id` | uuid | no | none | FK to `users` |
| `content` | text | no | none | Memory text |
| `tags` | jsonb | no | `[]` | Tags |
| `importance` | integer | no | 1 | 1 to 5 |
| `last_used_at` | timestamptz | no | now | Retrieval/use time |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Indexes:

- `memories_user_importance_used_idx` on `(user_id, importance, last_used_at DESC)`
- `memories_user_updated_idx` on `(user_id, updated_at DESC)`

Vector storage: Milvus collection `user_memories` (dim: 4096).

### Table: `memory_settings`

Per-user memory controls.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `user_id` | uuid | no | none | FK to `users` |
| `is_enabled` | boolean | no | true | Memory retrieval enabled |
| `max_tokens` | integer | no | 2000 | Context budget |
| `auto_save` | boolean | no | true | Auto-save memories |
| `auto_save_filter` | varchar(30) | no | `medium` | `low`, `medium`, `high` |
| `allow_tool_updates` | boolean | no | true | Allow `memory.save` tool |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Constraints:

- unique `user_id`

## Knowledge

### Table: `knowledge_sources`

Logical source collection.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `owner_id` | uuid | yes | null | Null allowed for global admin source |
| `name` | varchar(255) | no | none | Source name |
| `source_type` | varchar(50) | no | `upload` | `upload`, `folder`, `manual`, `api` |
| `visibility` | varchar(30) | no | `private` | `private`, `shared`, `global` |
| `metadata` | jsonb | no | `{}` | Extra source data |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Indexes:

- `knowledge_sources_owner_type_idx` on `(owner_id, source_type)`
- `knowledge_sources_visibility_idx` on `visibility`

Rule: user uploads default to `private`.

### Table: `documents`

Canonical ingested documents.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `source_id` | uuid | no | none | FK to `knowledge_sources` |
| `owner_id` | uuid | yes | null | FK to `users` |
| `title` | varchar(512) | no | none | Display title |
| `original_filename` | varchar(255) | yes | null | Upload filename |
| `mime_type` | varchar(255) | no | none | MIME type |
| `file` | varchar(1024) | yes | null | Django storage path |
| `sha256` | char(64) | yes | null | File hash |
| `status` | varchar(30) | no | `pending` | `pending`, `processing`, `ready`, `failed` |
| `extracted_text` | text | no | empty | Full extracted text |
| `analysis_summary` | text | no | empty | Model/OCR summary |
| `metadata` | jsonb | no | `{}` | Pages, parser, model metadata |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Indexes:

- `documents_owner_created_idx` on `(owner_id, created_at DESC)`
- `documents_source_status_idx` on `(source_id, status)`
- `documents_sha256_idx` on `sha256`

### Table: `document_assets`

Extracted images, pages, tables, or other multimodal assets.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `document_id` | uuid | no | none | FK to `documents` |
| `asset_type` | varchar(30) | no | none | `image`, `page`, `table`, `audio`, `video_frame` |
| `file` | varchar(1024) | yes | null | Asset path |
| `mime_type` | varchar(255) | yes | null | Asset MIME |
| `page_number` | integer | yes | null | Page reference |
| `text` | text | no | empty | Extracted text |
| `analysis` | text | no | empty | Vision/table analysis |
| `metadata` | jsonb | no | `{}` | Parser/model metadata |
| `created_at` | timestamptz | no | now | Created timestamp |

Indexes:

- `document_assets_document_type_idx` on `(document_id, asset_type)`
- `document_assets_document_page_idx` on `(document_id, page_number)`

### Table: `document_chunks`

Retrievable text units.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `document_id` | uuid | no | none | FK to `documents` |
| `asset_id` | uuid | yes | null | FK to `document_assets` |
| `chunk_index` | integer | no | none | Order in document |
| `content` | text | no | none | Chunk text |
| `content_hash` | char(64) | no | none | Hash of normalized content |
| `token_count` | integer | no | 0 | Estimated tokens |
| `metadata` | jsonb | no | `{}` | Page, section, source citation |
| `created_at` | timestamptz | no | now | Created timestamp |

Constraints:

- unique `(document_id, chunk_index)`

Indexes:

- `document_chunks_document_index_idx` on `(document_id, chunk_index)`
- `document_chunks_content_hash_idx` on `content_hash`

Vector storage: Milvus collection `document_chunks` (dim: 4096). The chunk's UUID serves as the Milvus document ID.

## Ingestion

### Table: `ingestion_jobs`

Background processing queue.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `document_id` | uuid | no | none | FK to `documents` |
| `status` | varchar(30) | no | `queued` | `queued`, `running`, `succeeded`, `failed` |
| `attempts` | integer | no | 0 | Attempt count |
| `error` | text | no | empty | Sanitized last error |
| `metadata` | jsonb | no | `{}` | Worker/parser metadata |
| `started_at` | timestamptz | yes | null | Start time |
| `finished_at` | timestamptz | yes | null | End time |
| `created_at` | timestamptz | no | now | Created timestamp |
| `updated_at` | timestamptz | no | now | Updated timestamp |

Indexes:

- `ingestion_jobs_status_created_idx` on `(status, created_at)`
- `ingestion_jobs_document_created_idx` on `(document_id, created_at DESC)`

## Retrieval

### Table: `retrieval_runs`

One retrieval operation, often created by `rag.search`.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `user_id` | uuid | no | none | FK to `users` |
| `chat_id` | uuid | yes | null | FK to `chats` |
| `message_id` | uuid | yes | null | FK to `messages` |
| `tool_call_id` | uuid | yes | null | FK to `tool_calls` |
| `query` | text | no | none | Search text |
| `query_embedding` | jsonb | yes | null | Query vector (cached) |
| `strategy` | varchar(30) | no | `hybrid` | `vector`, `text`, `hybrid` |
| `metadata` | jsonb | no | `{}` | Attempts, filters, thresholds |
| `created_at` | timestamptz | no | now | Created timestamp |

Indexes:

- `retrieval_runs_user_created_idx` on `(user_id, created_at DESC)`
- `retrieval_runs_chat_created_idx` on `(chat_id, created_at DESC)`
- `retrieval_runs_tool_call_idx` on `tool_call_id`

### Table: `retrieval_hits`

Chunks selected by a retrieval run.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `run_id` | uuid | no | none | FK to `retrieval_runs` |
| `chunk_id` | uuid | no | none | FK to `document_chunks` |
| `rank` | integer | no | none | Rank order |
| `score` | double precision | no | 0 | Combined score |
| `source_title` | varchar(512) | no | none | Snapshot citation |
| `source_metadata` | jsonb | no | `{}` | Page/source metadata |
| `created_at` | timestamptz | no | now | Created timestamp |

Constraints:

- unique `(run_id, chunk_id)`

Indexes:

- `retrieval_hits_run_rank_idx` on `(run_id, rank)`
- `retrieval_hits_chunk_idx` on `chunk_id`

## Compaction

### Table: `chat_compactions`

Versioned summary of older chat context.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `chat_id` | uuid | no | none | FK to `chats` |
| `from_message_id` | uuid | no | none | First summarized message |
| `to_message_id` | uuid | no | none | Last summarized message |
| `summary` | text | no | none | Compact summary |
| `facts` | jsonb | no | `[]` | Durable facts |
| `open_questions` | jsonb | no | `[]` | Unresolved items |
| `token_count` | integer | no | 0 | Estimated summary tokens |
| `model` | varchar(255) | no | none | Compaction model |
| `created_at` | timestamptz | no | now | Created timestamp |

Indexes:

- `chat_compactions_chat_created_idx` on `(chat_id, created_at DESC)`
- `chat_compactions_chat_range_idx` on `(chat_id, from_message_id, to_message_id)`

## LLM Accounting

### Table: `token_usage`

Token accounting across chat, embeddings, tools, memory, and compaction.

| Column | Type | Null | Default | Notes |
| --- | --- | --- | --- | --- |
| `id` | uuid | no | generated | Primary key |
| `user_id` | uuid | yes | null | FK to `users` |
| `chat_id` | uuid | yes | null | FK to `chats` |
| `message_id` | uuid | yes | null | FK to `messages` |
| `tool_call_id` | uuid | yes | null | FK to `tool_calls` |
| `operation` | varchar(50) | no | none | `chat`, `embedding`, `vision_analysis`, `compaction`, `memory`, `tool_call` |
| `provider` | varchar(100) | no | `litellm` | Provider/wrapper |
| `model` | varchar(255) | yes | null | Model name |
| `request_id` | varchar(255) | yes | null | Provider request ID |
| `step_index` | integer | yes | null | Multi-step call index |
| `input_tokens` | integer | no | 0 | Prompt tokens |
| `output_tokens` | integer | no | 0 | Completion tokens |
| `total_tokens` | integer | no | 0 | Total tokens |
| `metadata` | jsonb | no | `{}` | Extra accounting details |
| `created_at` | timestamptz | no | now | Created timestamp |

Indexes:

- `token_usage_created_idx` on `created_at`
- `token_usage_user_created_idx` on `(user_id, created_at DESC)`
- `token_usage_chat_created_idx` on `(chat_id, created_at DESC)`
- `token_usage_model_idx` on `model`
- `token_usage_operation_idx` on `operation`
- `token_usage_tool_call_idx` on `tool_call_id`

## Access Rules

Knowledge access:

- `private`: only owner.
- `shared`: owner and explicitly granted users/groups if sharing is later implemented.
- `global`: all authenticated users.

Tool access:

- only enabled tools are exposed to the model,
- permission is checked before execution,
- tool results must enforce the same knowledge ownership rules as normal views,
- public share viewers cannot execute tools against private owner data.

Memory access:

- only the owning user can retrieve or update memories,
- `memory.save` must respect `MemorySettings`.

## Deletion Rules

- Deleting a user cascades private chats, messages, memories, private knowledge sources, tool calls, and token usage linked only to that user.
- Deleting a chat cascades messages, deltas, votes, shares, compactions, retrieval runs, tool calls, and chat token usage.
- Deleting a document cascades assets, chunks, and ingestion jobs.
- Deleting a tool definition should be restricted if historical calls exist.
- Public share links should be revoked rather than deleted when possible.
- Deleting a document or memory should also remove its vector from Milvus.
