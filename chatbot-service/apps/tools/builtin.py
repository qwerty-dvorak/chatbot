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

from apps.documents.models import DocumentReference

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
        from apps.memory.services import save_memory
        mem = save_memory(user, content, importance)
        return {
            "saved":   True,
            "id":      str(mem.id),
            "content": mem.content,
            "message": "Memory saved successfully.",
        }
    except Exception as exc:
        logger.exception("memory.save failed")
        return {"saved": False, "error": str(exc)}


# ── memory.aggregate ──────────────────────────────────────────────────────────

@register_builtin("memory.aggregate")
def memory_aggregate(arguments: dict[str, Any], context: dict = {}) -> dict:
    """Scan recent chat history for user preferences/facts and save as memories."""
    user = context.get("user")
    chat = context.get("chat")

    if not user:
        return {"aggregated": False, "message": "No user context."}

    try:
        from apps.chat.models import Message
        from apps.memory.services import save_memory
        from apps.llm.clients import LiteLLMClient

        recent = Message.objects.filter(
            chat__user=user, role=Message.Role.USER,
            status=Message.Status.COMPLETED,
        ).order_by("-created_at")[:20]

        if not recent:
            return {"aggregated": False, "message": "No chat history to analyze."}

        chat_text = "\n".join(
            f"User: {m.content[:500]}" for m in reversed(recent)
        )

        client = LiteLLMClient()
        response = client.chat_completion(
            messages=[{"role": "user", "content": (
                "Extract factual statements about the user from this chat history. "
                "Return a JSON array of objects with 'content' (the fact) and 'importance' (1-5). "
                "Focus on preferences, interests, goals, and personal context. "
                "Return [] if nothing can be extracted.\n\n"
                f"Chat history:\n{chat_text}"
            )}],
            max_tokens=1000,
            temperature=0.1,
        )
        content = response.get("content", "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if "```" in content:
                content = content.split("```")[0]

        import json
        facts = json.loads(content) if content else []
        saved = []
        for fact in facts:
            mem = save_memory(user, fact.get("content", ""), int(fact.get("importance", 1)))
            saved.append({"content": mem.content, "importance": mem.importance})

        return {
            "aggregated": True,
            "count": len(saved),
            "memories": saved,
            "message": f"Extracted and saved {len(saved)} memories from chat history.",
        }
    except Exception as exc:
        logger.exception("memory.aggregate failed")
        return {"aggregated": False, "error": str(exc)}


# ── knowledge.ingest_status ────────────────────────────────────────────────────

@register_builtin("knowledge.ingest_status")
def ingest_status(arguments: dict[str, Any], context: dict = {}) -> dict:
    user = context.get("user")
    try:
        from apps.ingestion.models import IngestionJob

        doc_qs = DocumentReference.objects.all()
        if user:
            doc_qs = doc_qs.filter(owner=user)

        counts = {
            "documents_total":  doc_qs.count(),
            "pending":   doc_qs.filter(artifact_revision__processing_status="pending").count(),
            "processing": doc_qs.filter(artifact_revision__processing_status="processing").count(),
            "ready":     doc_qs.filter(artifact_revision__processing_status="ready").count(),
            "failed":    doc_qs.filter(artifact_revision__processing_status="failed").count(),
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
        from apps.compaction.services import compact_chat, context_usage

        before = context_usage(chat)
        compaction = compact_chat(chat)
        after = context_usage(chat)
        if not compaction:
            return {
                "compacted": False,
                "message": "Not enough uncompacted messages. At least 10 are required.",
                "context": after,
            }
        return {
            "compacted": True,
            "messages_compacted": len(
                list(
                    compaction.chat.messages.filter(
                        created_at__gte=compaction.from_message.created_at,
                        created_at__lte=compaction.to_message.created_at,
                    )
                )
            ),
            "summary_tokens": compaction.token_count,
            "context_before": before,
            "context_after": after,
            "message": "Older conversation context was summarized successfully.",
        }
    except Exception as exc:
        logger.exception("chat.compact failed")
        return {"compacted": False, "error": str(exc)}


# ── document.analyze ───────────────────────────────────────────────────────────

@register_builtin("document.analyze")
def document_analyze(arguments: dict[str, Any], context: dict = {}) -> dict:
    user        = context.get("user")
    document_id = arguments.get("document_id")

    try:
        qs = DocumentReference.objects.all()
        if user:
            qs = qs.filter(owner=user)
        if document_id and document_id != "latest":
            qs = qs.filter(id=document_id)
        doc_ref = qs.select_related("artifact_revision__blob").order_by("-created_at").first()
        if not doc_ref:
            return {"analyzed": False, "message": "No document found."}
        revision = doc_ref.artifact_revision
        return {
            "analyzed":   True,
            "title":      doc_ref.title,
            "status":     revision.processing_status,
            "mime_type":  revision.blob.mime_type,
            "summary":    revision.summary or revision.extracted_text[:500] or "No text extracted yet.",
        }
    except Exception as exc:
        logger.exception("document.analyze failed")
        return {"analyzed": False, "error": str(exc)}
