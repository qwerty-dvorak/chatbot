# API And Views

The first version should use Django server-rendered pages and standard HTML forms. Streaming chat is required and should use browser-native APIs with hand-written JavaScript only. JSON APIs can be added for internal tooling, but the product must not require npm or JavaScript packages.

## URL Layout

```text
/                         -> redirect to chat list or login
/accounts/register/        -> email/password registration
/accounts/login/           -> login
/accounts/logout/          -> logout
/accounts/password-reset/  -> password reset

/chats/                    -> chat list
/chats/new/                -> create chat
/chats/<chat_id>/          -> chat detail and message form
/chats/<chat_id>/stream/   -> streaming assistant response endpoint
/chats/<chat_id>/archive/  -> archive chat
/chats/<chat_id>/share/    -> create/revoke share
/share/<share_id>/         -> read-only shared chat

/knowledge/                -> document list
/knowledge/upload/         -> upload document
/knowledge/<document_id>/  -> document detail, chunks, status
/knowledge/search/         -> manual knowledge search
/tools/                    -> list available tools for the current user
/tools/calls/<call_id>/    -> inspect a tool call owned by the user

/memory/                   -> user memory list
/memory/settings/          -> memory settings
/memory/<memory_id>/edit/  -> edit memory

/admin/                    -> Django admin
```

## View Responsibilities

### Auth Views

Use Django auth views where possible:

- login,
- logout,
- password reset,
- password change.

Custom registration is needed because email is the login identifier.

### Chat Views

`ChatListView`

- Shows active and archived chats.
- Ordered by latest update.

`ChatCreateView`

- Creates a chat with a default title.
- Redirects to detail page.

`ChatDetailView`

- Displays messages.
- Displays uploaded attachments for each message.
- Shows message form.
- On POST, stores user message and creates a pending assistant message.
- For non-stream fallback, builds context, calls LiteLLM, stores assistant response, redirects back.

`ChatStreamView`

- Authenticates the user and chat ownership.
- Builds context and allowed tool list.
- Calls LiteLLM in streaming mode.
- Emits token deltas as SSE events.
- Persists deltas into `chat.MessageDelta`.
- Records tool calls, tool executions, and tool results as they happen.
- Finalizes the assistant message when generation completes.

`ChatArchiveView`

- Archives or restores a chat.

`ChatShareView`

- Creates or revokes a share row.
- Public share links must use unguessable tokens and must never expose private knowledge documents outside the stored chat content.

### Tool Views

`ToolListView`

- Shows tools available to the current user.
- Staff users can see disabled tools and schemas in admin.

`ToolCallDetailView`

- Shows arguments, status, result, error, and timing for a tool call owned by the user.
- Staff can inspect all calls through Django admin.

## Forms

### `MessageForm`

Fields:

- `content`
- optional file attachments
- optional knowledge source scope

Validation:

- content or attachment must be present,
- file size limit,
- allowed MIME types,
- user must own the chat.

### `DocumentUploadForm`

Fields:

- `file`
- `title`
- `visibility`
- `source`

Validation:

- allowed MIME type,
- max file size,
- private/shared/global permissions.

### `MemorySettingsForm`

Fields:

- `is_enabled`
- `max_tokens`
- `auto_save`
- `auto_save_filter`

## JSON Endpoints

Optional JSON endpoints for local tooling:

```text
GET  /api/health/
POST /api/chat/<chat_id>/message/
GET  /api/chat/<chat_id>/stream/<message_id>/
POST /api/knowledge/search/
GET  /api/knowledge/documents/<document_id>/status/
GET  /api/tools/
GET  /api/tools/calls/<call_id>/
GET  /api/stats/
```

These should be Django views, not a separate Node service.

## Streaming Behavior

Chat submission:

1. User submits form.
2. Server stores the user message and pending assistant message.
3. Browser opens the stream endpoint using native `EventSource` or fetch streaming.
4. Server sends token deltas and tool-call status events.
5. Browser progressively renders the assistant response.
6. Server stores final response, tool calls, retrieval runs, and token usage.

Non-stream fallback:

1. If streaming fails, the form can still submit and wait for a full response.
2. The final answer is rendered after generation.

Upload:

1. User submits file form.
2. Server creates document and job.
3. Page shows processing status.
4. User refreshes or revisits document page to see status.

This keeps npm out of the stack while still supporting streaming.

## Admin Screens

Django admin should expose:

- users,
- chats,
- messages,
- memories,
- memory settings,
- knowledge sources,
- documents,
- document assets,
- chunks,
- ingestion jobs,
- retrieval runs,
- tool definitions,
- tool calls,
- tool executions,
- tool results,
- token usage,
- security logs.

Admin actions:

- retry failed ingestion,
- re-embed selected documents,
- mark source global/private,
- archive selected chats,
- disable memory for selected users.
- enable or disable selected tools,
- retry failed background jobs where retry is safe.
