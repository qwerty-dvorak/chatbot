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
        from apps.memory.services import get_user_memories
        results = get_user_memories(user, query=query or None, top_k=top_k)

        memories = [
            {"content": m.content, "importance": m.importance,
             "created_at": m.created_at.isoformat()}
            for m in results
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
        from apps.llm.clients import ChatClient

        recent = Message.objects.filter(
            chat__user=user, role=Message.Role.USER,
            status=Message.Status.COMPLETED,
        ).order_by("-created_at")[:20]

        if not recent:
            return {"aggregated": False, "message": "No chat history to analyze."}

        chat_text = "\n".join(
            f"User: {m.content[:500]}" for m in reversed(recent)
        )

        client = ChatClient()
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


# ── knowledge.grep ─────────────────────────────────────────────────────────────

@register_builtin("knowledge.grep")
def knowledge_grep(arguments: dict[str, Any], context: dict = {}) -> dict:
    """Strict substring grep across user-uploaded knowledge documents.

    When RAG API is enabled, also searches the RAG pipeline for results.
    """
    pattern = arguments.get("pattern", "")
    top_k   = int(arguments.get("top_k", 10))
    case_sensitive = bool(arguments.get("case_sensitive", False))
    document_title = arguments.get("document_title", "")
    user    = context.get("user")

    if not pattern:
        return {"matches": [], "message": "No pattern provided."}

    try:
        from apps.knowledge.models import DocumentChunk

        qs = DocumentChunk.objects.select_related("document")

        if document_title:
            qs = qs.filter(document__title__icontains=document_title)

        if case_sensitive:
            qs = qs.filter(content__contains=pattern)
        else:
            qs = qs.filter(content__icontains=pattern)

        qs = qs.order_by("document__title", "chunk_index")[:top_k]

        matches = []
        for chunk in qs:
            snippet = chunk.content[:300]
            matches.append({
                "document_id":   str(chunk.document_id),
                "document_title": chunk.document.title,
                "chunk_index":   chunk.chunk_index,
                "snippet":       snippet,
                "match_count":   snippet.lower().count(pattern.lower()),
            })

        return {
            "pattern": pattern,
            "count":   len(matches),
            "matches": matches,
            "message": f"Found {len(matches)} chunk(s) matching '{pattern}'.",
        }
    except Exception as exc:
        logger.exception("knowledge.grep failed")
        return {"matches": [], "error": str(exc)}


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

def _find_document(user, document_id):
    """Look up a DocumentReference by UUID or title."""
    import uuid as _uuid
    try:
        _uuid.UUID(str(document_id))
        return DocumentReference.objects.filter(id=document_id, owner=user).first()
    except (ValueError, AttributeError):
        pass
    return DocumentReference.objects.filter(
        owner=user, title__iexact=document_id
    ).select_related("artifact_revision__blob").first()


def _ingest_from_attachment(chat, filename, user):
    """Find an attachment in chat messages and trigger RAG ingestion."""
    from django.conf import settings as dj_settings
    from apps.chat.models import Message
    from apps.documents.models import ContentBlob, ArtifactRevision, DocumentReference
    from apps.ingestion.models import IngestionJob
    from apps.knowledge.rag_client import rag_client
    from apps.knowledge.views import _docs_storage, _save_doc_file
    from django.core.files.base import ContentFile

    latest = Message.objects.filter(
        chat=chat, role=Message.Role.USER, status=Message.Status.COMPLETED
    ).order_by("-created_at").first()
    if not latest or not latest.attachments:
        return None

    att = None
    for a in latest.attachments:
        if a.get("original_filename", "").lower() == filename.lower():
            att = a
            break
    if not att:
        return None

    import hashlib
    from django.core.files.storage import default_storage
    file_path = att.get("file", "")
    if not default_storage.exists(file_path):
        return None

    sha256 = att.get("sha256", "")
    existing = ContentBlob.objects.filter(content_hash=sha256).first()
    doc_ref = None
    if existing:
        doc_ref = DocumentReference.objects.filter(
            artifact_revision__blob=existing, owner=user,
            kind=DocumentReference.Kind.KNOWLEDGE,
        ).first()
        if doc_ref:
            latest_job = IngestionJob.objects.filter(
                document_reference=doc_ref
            ).order_by("-created_at").first()
            if not latest_job or latest_job.status != "failed":
                return doc_ref

    with default_storage.open(file_path, "rb") as f:
        raw = f.read()
    mime = att.get("mime_type", "application/octet-stream")
    docs_storage = _docs_storage()
    rel_path = _save_doc_file(str(user.id), ContentFile(raw, att.get("original_filename", "file")))

    if not doc_ref:
        blob, _ = ContentBlob.objects.get_or_create(
            content_hash=sha256,
            defaults={
                "mime_type": mime,
                "size_bytes": att.get("size_bytes", len(raw)),
                "object_key": rel_path,
                "storage_status": ContentBlob.StorageStatus.STORED,
            },
        )
        revision, _ = ArtifactRevision.objects.get_or_create(
            blob=blob,
            pipeline_fingerprint=sha256[:16],
            defaults={
                "extracted_text": "",
                "processing_status": ArtifactRevision.ProcessingStatus.PENDING,
            },
        )
        doc_ref = DocumentReference.objects.create(
            owner=user,
            artifact_revision=revision,
            title=att.get("original_filename", "file"),
            kind=DocumentReference.Kind.KNOWLEDGE,
        )
    else:
        revision = doc_ref.artifact_revision
    if rag_client.is_enabled():
        import os
        docs_root = getattr(dj_settings, "DOCS_ROOT",
                            os.path.join(dj_settings.MEDIA_ROOT, "docs"))
        full_path = os.path.join(docs_root, rel_path)
        ingest_result = rag_client.ingest(full_path)
        job = None
        if ingest_result and ingest_result.get("id"):
            job = rag_client.poll_job(ingest_result["id"], max_retries=30, interval=0.5)
            IngestionJob.objects.create(
                document_reference=doc_ref,
                metadata={"rag_job_id": ingest_result["id"], "status": (job or {}).get("status", "unknown")},
            )
        if job and job.get("status") in ("succeeded", "completed"):
            revision.processing_status = ArtifactRevision.ProcessingStatus.READY
            revision.save(update_fields=["processing_status"])
    else:
        IngestionJob.objects.create(document_reference=doc_ref)
    return doc_ref

@register_builtin("document.analyze")
def document_analyze(arguments: dict[str, Any], context: dict = {}) -> dict:
    user        = context.get("user")
    chat        = context.get("chat")
    document_id = arguments.get("document_id", "").strip()

    if not user:
        return {"analyzed": False, "message": "No user context."}

    try:
        doc_ref = _find_document(user, document_id) if document_id else None
        if not doc_ref and document_id and chat:
            doc_ref = _ingest_from_attachment(chat, document_id, user)

        if not doc_ref:
            return {"analyzed": False, "message": f"Document '{document_id}' not found and no matching attachment to ingest."}

        revision = doc_ref.artifact_revision
        job = None
        from apps.ingestion.models import IngestionJob
        jobs = list(IngestionJob.objects.filter(document_reference=doc_ref).order_by("-created_at")[:1])
        job = jobs[0] if jobs else None

        # If still pending and no job, re-ingest from attachment
        if revision.processing_status in ("pending",) and not job and chat:
            doc_ref = _ingest_from_attachment(chat, document_id, user)
            if doc_ref:
                revision = doc_ref.artifact_revision
                jobs = list(IngestionJob.objects.filter(document_reference=doc_ref).order_by("-created_at")[:1])
                job = jobs[0] if jobs else None

        # Check RAG pipeline's KnowledgeDocument for real status
        from apps.knowledge.models import KnowledgeDocument
        rag_doc = KnowledgeDocument.objects.filter(
            sha256=revision.blob.content_hash
        ).order_by("-created_at").first() if revision.blob else None
        rag_ready = rag_doc is not None and rag_doc.status in ("ready", "completed", "succeeded")

        # Poll for completion
        import time as _time
        from apps.knowledge.rag_client import rag_client as _rag
        deadline = _time.time() + 60

        while _time.time() < deadline:
            if rag_ready:
                break
            rag_doc = KnowledgeDocument.objects.filter(
                sha256=revision.blob.content_hash
            ).order_by("-created_at").first() if revision.blob else None
            rag_ready = rag_doc is not None and rag_doc.status in ("ready", "completed", "succeeded")
            if rag_ready:
                break
            if job and job.metadata and job.metadata.get("rag_job_id") and _rag.is_enabled():
                try:
                    rag_status = _rag.get_job(job.metadata["rag_job_id"])
                    if rag_status:
                        rs = rag_status.get("status", "")
                        if rs in ("succeeded", "completed"):
                            rag_ready = True
                            break
                        if rs in ("failed", "error"):
                            revision.processing_status = ArtifactRevision.ProcessingStatus.FAILED
                            revision.save(update_fields=["processing_status"])
                            break
                except Exception:
                    pass
            _time.sleep(3)

        # Update revision status to match reality
        if rag_ready and revision.processing_status != ArtifactRevision.ProcessingStatus.READY:
            revision.processing_status = ArtifactRevision.ProcessingStatus.READY
            revision.save(update_fields=["processing_status"])

        revision.refresh_from_db()
        is_ready = revision.processing_status == ArtifactRevision.ProcessingStatus.READY
        summary = ""
        if rag_doc and rag_doc.analysis_summary:
            summary = rag_doc.analysis_summary[:500]
        elif rag_doc and rag_doc.extracted_text:
            summary = rag_doc.extracted_text[:500]
        elif revision.summary:
            summary = revision.summary[:500]
        elif revision.extracted_text:
            summary = revision.extracted_text[:500]

        return {
            "analyzed":   is_ready,
            "id":         str(doc_ref.id),
            "title":      doc_ref.title,
            "status":     revision.processing_status,
            "rag_status": rag_doc.status if rag_doc else "unknown",
            "mime_type":  revision.blob.mime_type if revision.blob else "",
            "summary":    summary or "No text extracted yet.",
        }
    except Exception as exc:
        logger.exception("document.analyze failed")
        return {"analyzed": False, "error": str(exc)}
