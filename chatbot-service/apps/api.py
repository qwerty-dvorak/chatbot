from django.conf import settings
from django.http import JsonResponse

from apps.documents.models import DocumentReference


def health_check(request):
    return JsonResponse({"status": "ok"})


def stats(request):
    from apps.accounts.models import User
    from apps.chat.models import Chat, Message

    return JsonResponse({
        "users": User.objects.count(),
        "chats": Chat.objects.count(),
        "messages": Message.objects.count(),
        "documents": DocumentReference.objects.count(),
    })


def openapi_json(request):
    """Combined OpenAPI document for the web app, RAG API, and file server."""
    web_url = request.build_absolute_uri("/").rstrip("/")
    rag_url = getattr(settings, "RAG_API_BASE_URL", "http://localhost:8093").rstrip("/")
    file_url = getattr(settings, "FILE_SERVER_BASE_URL", "http://localhost:8888").rstrip("/")
    return JsonResponse(_combined_openapi(web_url, rag_url, file_url))


def _combined_openapi(web_url: str, rag_url: str, file_url: str) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "BARC Chatbot Web, RAG, and File APIs",
            "version": "1.0.0",
            "description": (
                "Combined schema for the Django web application endpoints, "
                "the standalone RAG API, and the local document file server."
            ),
        },
        "servers": [{"url": web_url, "description": "Django web app"}],
        "tags": [
            {"name": "web", "description": "Django web application JSON and form endpoints"},
            {"name": "chat", "description": "Chat and message turn endpoints"},
            {"name": "rag", "description": "Standalone RAG pipeline API", "externalDocs": {"url": f"{rag_url}/openapi.json"}},
            {"name": "file-server", "description": "Local document file server", "externalDocs": {"url": f"{file_url}/openapi.json"}},
        ],
        "paths": {
            "/api/health/": {
                "get": {
                    "tags": ["web"],
                    "operationId": "webHealth",
                    "responses": {"200": {"description": "Service is live", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Health"}}}}},
                }
            },
            "/api/stats/": {
                "get": {
                    "tags": ["web"],
                    "operationId": "webStats",
                    "responses": {"200": {"description": "Application counts", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Stats"}}}}},
                }
            },
            "/chats/{chat_id}/": {
                "post": {
                    "tags": ["chat"],
                    "operationId": "sendChatMessage",
                    "summary": "Create a user message and queue an assistant response",
                    "parameters": [{"$ref": "#/components/parameters/ChatId"}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "content": {"type": "string"},
                                        "attachment": {"type": "array", "items": {"type": "string", "format": "binary"}},
                                        "thinking_mode": {"type": "boolean"},
                                        "knowledge_indices": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "302": {"description": "Redirects back to the chat"},
                        "200": {"description": "Validation errors rendered as HTML"},
                    },
                }
            },
            "/chats/{chat_id}/messages/{message_id}/edit/": {
                "post": {
                    "tags": ["chat"],
                    "operationId": "editLastUserMessage",
                    "summary": "Edit the latest user message and queue regeneration",
                    "parameters": [
                        {"$ref": "#/components/parameters/ChatId"},
                        {"$ref": "#/components/parameters/MessageId"},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EditMessageRequest"}}},
                    },
                    "responses": {
                        "200": {"description": "Message edited or unchanged", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EditMessageResponse"}}}},
                        "400": {"description": "Invalid request"},
                        "409": {"description": "Message is not editable in the current state"},
                    },
                }
            },
            "/chats/{chat_id}/stream/": {
                "get": {
                    "tags": ["chat"],
                    "operationId": "streamChatResponse",
                    "summary": "Stream the pending assistant response as server-sent events",
                    "parameters": [{"$ref": "#/components/parameters/ChatId"}],
                    "responses": {"200": {"description": "SSE stream", "content": {"text/event-stream": {"schema": {"type": "string"}}}}},
                }
            },
            "/chats/{chat_id}/cancel/": {
                "post": {
                    "tags": ["chat"],
                    "operationId": "cancelChatStream",
                    "parameters": [{"$ref": "#/components/parameters/ChatId"}],
                    "responses": {"200": {"description": "Stream cancelled"}, "404": {"description": "No active stream"}},
                }
            },
            "/chats/{chat_id}/attachment/{message_id}/{filename}": {
                "get": {
                    "tags": ["chat"],
                    "operationId": "getChatAttachment",
                    "parameters": [
                        {"$ref": "#/components/parameters/ChatId"},
                        {"$ref": "#/components/parameters/MessageId"},
                        {"name": "filename", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "Attachment bytes"}, "404": {"description": "Attachment not found"}},
                }
            },
            "/chats/{chat_id}/attachment/{message_id}/{index}/ingest/": {
                "post": {
                    "tags": ["chat"],
                    "operationId": "ingestChatAttachment",
                    "parameters": [
                        {"$ref": "#/components/parameters/ChatId"},
                        {"$ref": "#/components/parameters/MessageId"},
                        {"name": "index", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 0}},
                    ],
                    "responses": {"200": {"description": "Attachment ingested"}, "400": {"description": "Invalid attachment"}},
                }
            },
            "/v1/search": {
                "servers": [{"url": rag_url, "description": "RAG API"}],
                "post": {
                    "tags": ["rag"],
                    "operationId": "ragSearch",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RagSearchRequest"}}}},
                    "responses": {"200": {"description": "Search results", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RagSearchResponse"}}}}},
                },
            },
            "/v1/ingest": {
                "servers": [{"url": rag_url, "description": "RAG API"}],
                "post": {
                    "tags": ["rag"],
                    "operationId": "ragIngest",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "required": ["files"],
                                    "properties": {
                                        "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
                                        "tier": {"type": "string", "enum": ["instant", "slow", "global"]},
                                        "strategy": {"type": "string", "enum": ["recursive", "sentence_window", "hierarchical"]},
                                        "hypothetical_questions": {"type": "boolean"},
                                        "ocr_mode": {"type": "string", "enum": ["none", "basic", "paddleocr"]},
                                        "document_id": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"202": {"description": "Ingestion job queued"}},
                },
            },
            "/v1/ingestions": {
                "servers": [{"url": rag_url, "description": "RAG API"}],
                "get": {"tags": ["rag"], "operationId": "listRagIngestions", "responses": {"200": {"description": "Ingestion jobs"}}},
            },
            "/v1/ingestions/{job_id}": {
                "servers": [{"url": rag_url, "description": "RAG API"}],
                "get": {
                    "tags": ["rag"],
                    "operationId": "getRagIngestion",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Ingestion job"}},
                },
                "delete": {
                    "tags": ["rag"],
                    "operationId": "cancelRagIngestion",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Ingestion job cancelled"}},
                },
            },
            "/upload": {
                "servers": [{"url": file_url, "description": "File server"}],
                "post": {
                    "tags": ["file-server"],
                    "operationId": "fileServerUpload",
                    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/FileUploadRequest"}}}},
                    "responses": {"200": {"description": "Stored file", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FileUploadResponse"}}}}},
                },
            },
            "/browse": {
                "servers": [{"url": file_url, "description": "File server"}],
                "get": {
                    "tags": ["file-server"],
                    "operationId": "fileServerBrowse",
                    "parameters": [
                        {"name": "user", "in": "query", "schema": {"type": "string"}},
                        {"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}},
                    ],
                    "responses": {"200": {"description": "Stored file tree"}},
                },
            },
            "/files/{path}": {
                "servers": [{"url": file_url, "description": "File server"}],
                "get": {
                    "tags": ["file-server"],
                    "operationId": "fileServerGetFile",
                    "parameters": [{"name": "path", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "File bytes"}, "404": {"description": "File not found"}},
                },
                "delete": {
                    "tags": ["file-server"],
                    "operationId": "fileServerDeleteFile",
                    "parameters": [{"name": "path", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "File deleted"}, "404": {"description": "File not found"}},
                },
            },
        },
        "components": {
            "parameters": {
                "ChatId": {"name": "chat_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                "MessageId": {"name": "message_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
            },
            "schemas": {
                "Health": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]},
                "Stats": {
                    "type": "object",
                    "properties": {
                        "users": {"type": "integer"},
                        "chats": {"type": "integer"},
                        "messages": {"type": "integer"},
                        "documents": {"type": "integer"},
                    },
                    "required": ["users", "chats", "messages", "documents"],
                },
                "EditMessageRequest": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
                "EditMessageResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["edited", "unchanged"]},
                        "message_id": {"type": "string", "format": "uuid"},
                        "assistant_message_id": {"type": "string", "format": "uuid"},
                        "content": {"type": "string"},
                        "edit_count": {"type": "integer"},
                        "edited_at": {"type": "string", "format": "date-time"},
                        "stream_url": {"type": "string"},
                    },
                    "required": ["status", "message_id", "content"],
                },
                "RagSearchRequest": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 100, "default": 5},
                        "mode": {"type": "string", "enum": ["hybrid", "vector", "bm25"], "default": "hybrid"},
                        "use_reranker": {"type": "boolean", "default": True},
                        "hierarchical": {"type": "boolean", "default": True},
                        "hyde": {"type": "boolean", "default": True},
                        "sub_queries": {"type": "boolean", "default": True},
                        "stepback": {"type": "boolean", "default": True},
                        "artifact_sources": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
                "RagSearchResponse": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "enhanced_queries": {"type": "array", "items": {"type": "string"}},
                        "retrieval_mode": {"type": "string"},
                        "use_reranker": {"type": "boolean"},
                        "hierarchical": {"type": "boolean"},
                        "results": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                        "total": {"type": "integer"},
                        "timing": {"type": "object", "additionalProperties": True},
                    },
                },
                "FileUploadRequest": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "filename": {"type": "string"},
                        "user_id": {"type": "string"},
                        "category": {"type": "string", "default": "misc"},
                        "date": {"type": "string", "format": "date"},
                    },
                },
                "FileUploadResponse": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "path": {"type": "string"},
                        "size": {"type": "integer"},
                    },
                    "required": ["ok"],
                },
            },
        },
    }
