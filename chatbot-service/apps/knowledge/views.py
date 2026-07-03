import hashlib
import logging
import os
import uuid
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView

from django.db.models import Q

from apps.documents.models import ArtifactRevision, ContentBlob, DocumentReference
from apps.ingestion.models import IngestionJob
from apps.knowledge.models import KnowledgeDocument

from .rag_client import rag_client

logger = logging.getLogger(__name__)

ALLOWED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}


def _docs_storage():
    root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
    os.makedirs(root, exist_ok=True)
    return FileSystemStorage(location=root)


def _save_doc_file(user_id, uploaded_file):
    storage = _docs_storage()
    name, ext = os.path.splitext(uploaded_file.name)
    rel = os.path.join(str(user_id), str(date.today()), "knowledge", uploaded_file.name)  # noqa: DTZ011
    if storage.exists(rel):
        rel = os.path.join(
            str(user_id), str(date.today()), "knowledge",  # noqa: DTZ011
            f"{name}_{uuid.uuid4().hex[:6]}{ext}"
        )
    return storage.save(rel, uploaded_file)


def _global_knowledge_hashes() -> list[str]:
    return list(
        KnowledgeDocument.objects.filter(source__is_global=True)
        .exclude(sha256="")
        .values_list("sha256", flat=True)
    )


class DocumentListView(LoginRequiredMixin, ListView):
    model = DocumentReference
    template_name = "knowledge/document_list.html"
    context_object_name = "documents"

    def get_queryset(self):
        q = Q(owner=self.request.user)
        global_hashes = _global_knowledge_hashes()
        if global_hashes:
            q |= Q(artifact_revision__blob__content_hash__in=global_hashes)
        return DocumentReference.objects.filter(
            q, kind=DocumentReference.Kind.KNOWLEDGE
        ).select_related("artifact_revision__blob").order_by("-created_at")


class DocumentDetailView(LoginRequiredMixin, DetailView):
    model = DocumentReference
    template_name = "knowledge/document_detail.html"
    context_object_name = "document"

    def get_queryset(self):
        q = Q(owner=self.request.user)
        global_hashes = _global_knowledge_hashes()
        if global_hashes:
            q |= Q(artifact_revision__blob__content_hash__in=global_hashes)
        return DocumentReference.objects.filter(
            q, kind=DocumentReference.Kind.KNOWLEDGE
        ).select_related("artifact_revision__blob")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = (
            IngestionJob.objects.filter(document_reference=self.object)
            .order_by("-created_at")
            .first()
        )
        context["job"] = job
        context["rag_doc"] = None
        context["rag_timing"] = None
        context["timing_rows"] = []
        context["step_summary"] = None
        context["rag_steps"] = []
        if job:
            _sync_rag_job(job, self.object)
            rag_result = job.metadata.get("rag_result") or {}
            doc_id = rag_result.get("document_id")
            if doc_id:
                from apps.knowledge.models import KnowledgeDocument
                try:  # noqa: SIM105
                    context["rag_doc"] = KnowledgeDocument.objects.get(id=doc_id)
                except KnowledgeDocument.DoesNotExist:
                    pass
            if context["rag_doc"] is None:
                from apps.knowledge.models import KnowledgeDocument
                context["rag_doc"] = (
                    KnowledgeDocument.objects.filter(
                        original_filename=self.object.title,
                        metadata__object_key=self.object.artifact_revision.blob.content_hash,
                    )
                    .order_by("-created_at")
                    .first()
                )
            context["rag_timing"] = job.metadata.get("rag_timing")
            context["timing_rows"] = _timing_rows(context["rag_timing"])
            context["step_summary"] = job.metadata.get("rag_step_summary")
            context["rag_steps"] = job.metadata.get("rag_steps") or []
        return context


RAG_STATUS_MAP = {
    "queued": "pending",
    "running": "processing",
    "succeeded": "ready",
    "failed": "failed",
    "cancelled": "failed",
}


def _primary_rag_result(rag_data: dict | None) -> dict:
    result = (rag_data or {}).get("result") or {}
    results = result.get("results")
    if isinstance(results, list) and results:
        return results[0] or {}
    return result if isinstance(result, dict) else {}


def _find_rag_job(document: DocumentReference) -> dict | None:
    """Recover remote ingestion metadata for older local rows missing a job id."""
    if not rag_client.is_enabled():
        return None
    title = document.title
    content_hash = document.artifact_revision.blob.content_hash
    candidates = []
    for index, rag_data in enumerate(rag_client.list_jobs(limit=200)):
        request_doc_id = ((rag_data.get("request") or {}).get("document_id") or "")
        primary = _primary_rag_result(rag_data)
        object_key = primary.get("object_key", "")
        exact_file = title in (rag_data.get("files") or [])
        score = 0
        if request_doc_id == str(document.id):
            score += 100
        if object_key == content_hash:
            score += 50
        if exact_file:
            score += 20
        if rag_data.get("status") == "succeeded" and primary:
            score += 10
        if score >= 60 or (exact_file and rag_data.get("status") == "succeeded"):  # noqa: PLR2004
            candidates.append((score, -index, rag_data))
    return max(candidates, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]


def _sync_rag_job(job: IngestionJob, document: DocumentReference) -> dict | None:
    if not rag_client.is_enabled():
        return None
    rag_job_id = (job.metadata or {}).get("rag_job_id")
    rag_data = rag_client.get_job(rag_job_id) if rag_job_id else None
    if not rag_data:
        rag_data = _find_rag_job(document)
    if not rag_data:
        return None

    metadata = dict(job.metadata or {})
    metadata["rag_job_id"] = rag_data.get("id", rag_job_id)
    primary = _primary_rag_result(rag_data)
    if primary:
        metadata["rag_result"] = primary
        metadata["rag_timing"] = primary.get("timing") or {}
    metadata["rag_steps"] = rag_data.get("steps") or []
    metadata["rag_step_summary"] = rag_data.get("step_summary") or {}

    remote_status = rag_data.get("status")
    mapped_status = {
        "queued": IngestionJob.Status.QUEUED,
        "running": IngestionJob.Status.RUNNING,
        "succeeded": IngestionJob.Status.SUCCEEDED,
        "failed": IngestionJob.Status.FAILED,
        "cancelled": IngestionJob.Status.FAILED,
    }.get(remote_status)
    update_fields = []
    if metadata != job.metadata:
        job.metadata = metadata
        update_fields.append("metadata")
    if mapped_status and mapped_status != job.status:
        job.status = mapped_status
        update_fields.append("status")
    error = rag_data.get("error") or ""
    if error != job.error:
        job.error = error
        update_fields.append("error")
    if update_fields:
        job.save(update_fields=update_fields)
    return rag_data


def _timing_rows(timing: dict | None) -> list[dict]:
    rows = []
    for name, value in (timing or {}).items():
        if isinstance(value, dict):
            milliseconds = value.get("duration_ms")
            seconds = milliseconds / 1000 if isinstance(milliseconds, (int, float)) else None
        else:
            seconds = float(value) if isinstance(value, (int, float)) else None
        rows.append({"name": name, "seconds": seconds})
    return rows


class DocumentStatusJsonView(LoginRequiredMixin, DetailView):
    model = DocumentReference

    def get_queryset(self):
        q = Q(owner=self.request.user)
        global_hashes = _global_knowledge_hashes()
        if global_hashes:
            q |= Q(artifact_revision__blob__content_hash__in=global_hashes)
        return DocumentReference.objects.filter(
            q, kind=DocumentReference.Kind.KNOWLEDGE
        ).select_related("artifact_revision__blob")

    def render_to_response(self, context, **response_kwargs):
        doc = self.object
        rev = doc.artifact_revision
        job = (
            IngestionJob.objects.filter(document_reference=doc)
            .order_by("-created_at")
            .first()
        )
        job_status = job.status if job else None
        processing_status = rev.processing_status
        rag_status = None
        progress_pct = None
        current_step = None

        if job and rag_client.is_enabled():
            rag_data = _sync_rag_job(job, doc)
            if rag_data:
                rag_status = rag_data.get("status")
                mapping_status = RAG_STATUS_MAP.get(rag_status)
                if mapping_status and mapping_status != processing_status:
                    rev.processing_status = mapping_status
                    rev.save(update_fields=["processing_status"])
                    processing_status = mapping_status

                step_summary = rag_data.get("step_summary") or {}
                steps = rag_data.get("steps") or []
                ss_total = step_summary.get("total", 0)
                ss_done = step_summary.get("completed", 0)
                ss_failed = step_summary.get("failed", 0)
                if ss_total > 0:
                    progress_pct = round((ss_done + ss_failed) / ss_total * 100)
                current_step = step_summary.get("current_step") or ""
                if not current_step and steps:
                    for s in steps:
                        if s.get("status") in ("running", "pending"):
                            current_step = s.get("name", "")
                            break

                job_status = job.status

        data = {
            "id": str(doc.id),
            "processing_status": processing_status,
            "job_status": job_status,
            "rag_status": rag_status,
            "has_summary": bool(rev.summary),
            "summary": rev.summary or "",
            "progress_pct": progress_pct,
            "current_step": current_step,
        }
        if job and job.metadata:
            meta = job.metadata
            if "rag_result" in meta:
                data["rag_document_id"] = meta["rag_result"].get("document_id")
                data["chunks_created"] = meta["rag_result"].get("chunks_created")
                data["embeddings_indexed"] = meta["rag_result"].get("embeddings_indexed")
                data["rag_summary"] = meta["rag_result"].get("summary_text", "")
            if "rag_timing" in meta:
                data["rag_timing"] = meta["rag_timing"]
            data["steps"] = meta.get("rag_steps") or []
            data["step_summary"] = meta.get("rag_step_summary") or {}
        return JsonResponse(data)


class DocumentUploadView(LoginRequiredMixin, View):
    template_name = "knowledge/upload.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        title = request.POST.get("title", "").strip()
        uploaded_files = request.FILES.getlist("files") or request.FILES.getlist("file")
        ocr_mode = request.POST.get("ocr_mode", "paddleocr")
        if ocr_mode not in ("none", "basic", "paddleocr"):
            ocr_mode = "paddleocr"

        if not uploaded_files:
            return render(request, self.template_name, {"error": "Please select a file."})

        for uploaded_file in uploaded_files:
            mime = uploaded_file.content_type or "application/octet-stream"
            if mime not in ALLOWED_MIME:
                return render(request, self.template_name,
                              {"error": f"File type '{mime}' is not supported: {uploaded_file.name}."})
            if uploaded_file.size > 50 * 1024 * 1024:
                return render(request, self.template_name,
                              {"error": f"File must be under 50 MB: {uploaded_file.name}."})

        for uploaded_file in uploaded_files:
            mime = uploaded_file.content_type or "application/octet-stream"
            sha256 = hashlib.sha256(uploaded_file.read()).hexdigest()
            uploaded_file.seek(0)
            document_title = title if title and len(uploaded_files) == 1 else uploaded_file.name

            existing = ContentBlob.objects.filter(content_hash=sha256).first()
            if existing:
                dup = DocumentReference.objects.filter(
                    artifact_revision__blob=existing, owner=request.user,
                    kind=DocumentReference.Kind.KNOWLEDGE,
                ).first()
                if dup:
                    return render(request, self.template_name, {
                        "error": f"'{uploaded_file.name}' was already uploaded as "
                                 f"<a href=\"{reverse_lazy('knowledge:detail', args=[dup.id])}\">'{dup.title}'</a>."
                                 " Delete the existing document first to re-upload.",
                    })

            rel_path = _save_doc_file(request.user.id, uploaded_file)

            blob, _ = ContentBlob.objects.get_or_create(
                content_hash=sha256,
                defaults={
                    "mime_type": mime,
                    "size_bytes": uploaded_file.size,
                    "object_key": rel_path,
                    "storage_status": ContentBlob.StorageStatus.STORED,
                },
            )
            revision, rev_created = ArtifactRevision.objects.get_or_create(
                blob=blob,
                pipeline_fingerprint=sha256[:16],
                defaults={
                    "extracted_text": "",
                    "processing_status": ArtifactRevision.ProcessingStatus.PENDING,
                },
            )
            if not rev_created:
                revision.processing_status = ArtifactRevision.ProcessingStatus.PENDING
                revision.extracted_text = ""
                revision.summary = ""
                revision.save(update_fields=["processing_status", "extracted_text", "summary"])
            doc_ref = DocumentReference.objects.create(
                owner=request.user,
                artifact_revision=revision,
                title=document_title,
                kind=DocumentReference.Kind.KNOWLEDGE,
            )
            if rag_client.is_enabled():
                generate_summary = request.POST.get("generate_summary", "1") == "1"
                docs_root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
                full_path = os.path.join(docs_root, rel_path)
                result = rag_client.ingest(full_path, ocr_mode=ocr_mode, generate_summary=generate_summary)
                if result and (job_id := result.get("id")):
                    IngestionJob.objects.create(
                        document_reference=doc_ref,
                        metadata={"rag_job_id": job_id},
                    )
                else:
                    logger = __import__("logging").getLogger(__name__)
                    logger.error("RAG API ingest failed for %s — result=%s", full_path, result)
                    messages.error(request, f"Ingestion queuing failed for {uploaded_file.name}. Check logs.")
            else:
                IngestionJob.objects.create(document_reference=doc_ref)

        messages.success(request, f"{len(uploaded_files)} file(s) uploaded — processing started.")
        return redirect("knowledge:list")


class IngestionQueueView(LoginRequiredMixin, View):
    """HTML page for queue management."""
    template_name = "knowledge/queue.html"

    def get(self, request):
        from django.shortcuts import render
        return render(request, self.template_name)


class IngestionQueueJsonView(LoginRequiredMixin, View):
    """JSON endpoint for queue management."""

    def get(self, request, job_id=None):
        q = Q(document_reference__owner=request.user)
        global_hashes = _global_knowledge_hashes()
        if global_hashes:
            q |= Q(document_reference__artifact_revision__blob__content_hash__in=global_hashes)
        jobs = IngestionJob.objects.filter(
            q,
            document_reference__kind=DocumentReference.Kind.KNOWLEDGE,
        ).select_related("document_reference").order_by("queue_order", "-created_at")[:100]

        if rag_client.is_enabled():
            for job in jobs:
                _sync_rag_job(job, job.document_reference)

        items = []
        for job in jobs:
            rag_steps = (job.metadata or {}).get("rag_steps") or []
            step_summary = (job.metadata or {}).get("rag_step_summary") or {}
            items.append({
                "id": str(job.id),
                "document_id": str(job.document_reference.id),
                "title": job.document_reference.title,
                "status": job.status,
                "error": job.error,
                "queue_order": job.queue_order,
                "progress_pct": step_summary.get("progress_pct"),
                "current_step": step_summary.get("current_step", ""),
                "steps": rag_steps,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            })
        return JsonResponse({"jobs": items})

    def delete(self, request, job_id=None):
        if not job_id:
            return JsonResponse({"error": "job_id required"}, status=400)
        job = get_object_or_404(
            IngestionJob, id=job_id,
            document_reference__owner=request.user,
        )
        if job.status in (IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING):
            if rag_client.is_enabled() and job.metadata:
                rag_job_id = job.metadata.get("rag_job_id")
                if rag_job_id:
                    rag_client.cancel_job(rag_job_id)
            job.status = IngestionJob.Status.FAILED
            job.error = "Cancelled by user"
            job.save(update_fields=["status", "error"])
        return JsonResponse({"status": "cancelled"})

    def post(self, request, job_id=None):
        import json as json_mod
        body = json_mod.loads(request.body) if request.body else {}
        action = body.get("action", "")
        if action == "reorder":
            order = body.get("order", [])
            for idx, job_id_str in enumerate(order):
                IngestionJob.objects.filter(
                    id=job_id_str,
                    document_reference__owner=request.user,
                ).update(queue_order=idx)
            return JsonResponse({"status": "reordered"})
        return JsonResponse({"error": "unknown action"}, status=400)

    def patch(self, request, job_id=None):
        if not job_id:
            return JsonResponse({"error": "job_id required"}, status=400)
        job = get_object_or_404(
            IngestionJob, id=job_id,
            document_reference__owner=request.user,
        )
        import json as json_mod
        body = json_mod.loads(request.body) if request.body else {}
        action = body.get("action", "")
        if action == "cancel":
            return self.delete(request, job_id)
        if action == "delete":
            doc_ref = job.document_reference
            job.delete()
            if doc_ref:
                doc_ref.delete()
            return JsonResponse({"status": "deleted"})
        return JsonResponse({"error": "unknown action"}, status=400)


class GlobalKnowledgeStatusView(View):
    """Public JSON endpoint showing global knowledge status — no auth required."""

    def get(self, request):
        documents = KnowledgeDocument.objects.filter(
            source__is_global=True,
        ).order_by("-created_at")
        items = []
        for doc in documents:
            chunk_count = doc.chunks.count()
            refs = DocumentReference.objects.filter(
                artifact_revision__blob__content_hash=doc.sha256,
                kind=DocumentReference.Kind.KNOWLEDGE,
            )
            job = IngestionJob.objects.filter(
                document_reference__in=refs,
            ).order_by("-created_at").first()
            items.append({
                "id": str(doc.id),
                "title": doc.title or doc.original_filename,
                "status": doc.status,
                "sha256": doc.sha256[:16],
                "mime_type": doc.mime_type,
                "chunks": chunk_count,
                "has_summary": bool(doc.analysis_summary),
                "summary": (doc.analysis_summary[:500] + "…") if doc.analysis_summary and len(doc.analysis_summary) > 500 else (doc.analysis_summary or ""),
                "job_status": job.status if job else None,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
            })
        return JsonResponse({"documents": items})


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        q = Q(id=pk, owner=request.user)
        global_hashes = _global_knowledge_hashes()
        if global_hashes:
            q |= Q(id=pk, artifact_revision__blob__content_hash__in=global_hashes)
        doc_ref = DocumentReference.objects.filter(q).first()
        if doc_ref:
            if doc_ref.owner != request.user:
                messages.error(request, "This document is global knowledge and cannot be deleted.")
                return redirect("knowledge:list")
            content_hash = doc_ref.artifact_revision.blob.content_hash if doc_ref.artifact_revision and doc_ref.artifact_revision.blob else None
            if content_hash:
                is_global = KnowledgeDocument.objects.filter(
                    sha256=content_hash, source__is_global=True,
                ).exists()
                if is_global:
                    messages.error(request, "This document is global knowledge and cannot be deleted.")
                    return redirect("knowledge:list")
            pending_jobs = IngestionJob.objects.filter(
                document_reference=doc_ref,
                status__in=[IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING],
            )
            for job in pending_jobs:
                if rag_client.is_enabled():
                    rag_client.cancel_job(str(job.id))
                job.status = IngestionJob.Status.FAILED
                job.error = "Cancelled by user"
                job.save(update_fields=["status", "error"])

            doc_ref.delete()
            messages.success(request, "Document deleted.")
        return redirect("knowledge:list")
