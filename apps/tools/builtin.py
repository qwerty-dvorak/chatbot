"""
Built-in tool handlers.

Each handler receives (arguments: dict, context: dict) where context contains:
  - context["user"]  — the authenticated User instance (may be None)
  - context["chat"]  — the Chat instance (may be None)

Return a dict (serialised to JSON) or a plain string.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

BUILTIN_TOOLS: dict[str, callable] = {}


def register_builtin(name: str):
    def decorator(func):
        BUILTIN_TOOLS[name] = func
        return func
    return decorator


# ── memory.search ──────────────────────────────────────────────────────────────

@register_builtin("memory.search")
def memory_search(arguments: dict[str, Any], context: dict = {}) -> dict:
    query  = arguments.get("query", "")
    top_k  = int(arguments.get("top_k", 5))
    user   = context.get("user")

    if not user:
        return {"memories": [], "message": "No user context."}

    try:
        from apps.memory.models import Memory
        qs = Memory.objects.filter(user=user)
        if query:
            qs = qs.filter(content__icontains=query)
        qs = qs.order_by("-importance", "-last_used_at")[:top_k]

        memories = [
            {"content": m.content, "importance": m.importance,
             "created_at": m.created_at.isoformat()}
            for m in qs
        ]
        return {
            "query":    query,
            "count":    len(memories),
            "memories": memories,
            "message":  f"Found {len(memories)} memory record(s) matching '{query}'.",
        }
    except Exception as exc:
        logger.exception("memory.search failed")
        return {"memories": [], "error": str(exc)}


# ── memory.save ────────────────────────────────────────────────────────────────

@register_builtin("memory.save")
def memory_save(arguments: dict[str, Any], context: dict = {}) -> dict:
    content    = arguments.get("content", "").strip()
    importance = int(arguments.get("importance", 1))
    user       = context.get("user")

    if not content:
        return {"saved": False, "message": "No content provided."}
    if not user:
        return {"saved": False, "message": "No user context."}

    try:
        from apps.memory.models import Memory
        mem = Memory.objects.create(user=user, content=content, importance=importance)
        return {
            "saved":   True,
            "id":      str(mem.id),
            "content": mem.content,
            "message": "Memory saved successfully.",
        }
    except Exception as exc:
        logger.exception("memory.save failed")
        return {"saved": False, "error": str(exc)}


# ── rag.search ─────────────────────────────────────────────────────────────────

@register_builtin("rag.search")
def rag_search(arguments: dict[str, Any], context: dict = {}) -> dict:
    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", 5))
    user  = context.get("user")

    if not query:
        return {"results": [], "message": "No query provided."}

    try:
        from apps.knowledge.models import DocumentChunk
        qs = DocumentChunk.objects.select_related("document")
        if user:
            qs = qs.filter(document__owner=user)
        if query:
            qs = qs.filter(content__icontains=query)
        qs = qs.order_by("document", "chunk_index")[:top_k]

        results = [
            {
                "document": c.document.title,
                "chunk":    c.chunk_index,
                "excerpt":  c.content[:300],
            }
            for c in qs
        ]
        return {
            "query":   query,
            "count":   len(results),
            "results": results,
            "message": f"Found {len(results)} chunk(s) matching '{query}'.",
        }
    except Exception as exc:
        logger.exception("rag.search failed")
        return {"results": [], "error": str(exc)}


# ── knowledge.ingest_status ────────────────────────────────────────────────────

@register_builtin("knowledge.ingest_status")
def ingest_status(arguments: dict[str, Any], context: dict = {}) -> dict:
    user = context.get("user")
    try:
        from apps.ingestion.models import IngestionJob
        from apps.knowledge.models import Document

        doc_qs = Document.objects.all()
        job_qs = IngestionJob.objects.all()
        if user:
            doc_qs = doc_qs.filter(owner=user)
            job_qs = job_qs.filter(document__owner=user)

        counts = {
            "documents_total":  doc_qs.count(),
            "pending":   doc_qs.filter(status="pending").count(),
            "processing": doc_qs.filter(status="processing").count(),
            "ready":     doc_qs.filter(status="ready").count(),
            "failed":    doc_qs.filter(status="failed").count(),
            "jobs_queued":  job_qs.filter(status="queued").count(),
            "jobs_running": job_qs.filter(status="running").count(),
            "jobs_failed":  job_qs.filter(status="failed").count(),
        }
        return {"status": "ok", "counts": counts}
    except Exception as exc:
        logger.exception("knowledge.ingest_status failed")
        return {"status": "error", "error": str(exc)}


# ── chat.compact ───────────────────────────────────────────────────────────────

@register_builtin("chat.compact")
def chat_compact(arguments: dict[str, Any], context: dict = {}) -> dict:
    chat = context.get("chat")
    if not chat:
        return {"compacted": False, "message": "No chat context."}
    try:
        from apps.chat.models import Message
        count = Message.objects.filter(chat=chat, status="completed").count()
        return {
            "compacted": False,
            "message":   f"Chat has {count} completed messages. "
                         "Compaction would summarise older messages to free context.",
        }
    except Exception as exc:
        return {"compacted": False, "error": str(exc)}


# ── document.analyze ───────────────────────────────────────────────────────────

@register_builtin("document.analyze")
def document_analyze(arguments: dict[str, Any], context: dict = {}) -> dict:
    user        = context.get("user")
    document_id = arguments.get("document_id")

    try:
        from apps.knowledge.models import Document
        qs = Document.objects.all()
        if user:
            qs = qs.filter(owner=user)
        if document_id and document_id != "latest":
            qs = qs.filter(id=document_id)
        doc = qs.order_by("-created_at").first()
        if not doc:
            return {"analyzed": False, "message": "No document found."}
        return {
            "analyzed":   True,
            "title":      doc.title,
            "status":     doc.status,
            "mime_type":  doc.mime_type,
            "chunks":     doc.chunks.count(),
            "summary":    doc.analysis_summary or doc.extracted_text[:500] or "No text extracted yet.",
        }
    except Exception as exc:
        logger.exception("document.analyze failed")
        return {"analyzed": False, "error": str(exc)}
