import hashlib
import os
import uuid
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView

from apps.documents.models import ArtifactRevision, ContentBlob, DocumentGrant, DocumentReference
from apps.ingestion.models import IngestionJob

from .rag_client import rag_client

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
    rel = os.path.join(str(user_id), str(date.today()), "knowledge", uploaded_file.name)
    if storage.exists(rel):
        rel = os.path.join(
            str(user_id), str(date.today()), "knowledge",
            f"{name}_{uuid.uuid4().hex[:6]}{ext}"
        )
    return storage.save(rel, uploaded_file)


class DocumentListView(LoginRequiredMixin, ListView):
    model = DocumentReference
    template_name = "knowledge/document_list.html"
    context_object_name = "documents"

    def get_queryset(self):
        return DocumentReference.objects.filter(
            owner=self.request.user, kind=DocumentReference.Kind.KNOWLEDGE
        ).select_related("artifact_revision__blob").order_by("-created_at")


class DocumentDetailView(LoginRequiredMixin, DetailView):
    model = DocumentReference
    template_name = "knowledge/document_detail.html"
    context_object_name = "document"

    def get_queryset(self):
        return DocumentReference.objects.filter(
            owner=self.request.user, kind=DocumentReference.Kind.KNOWLEDGE
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
        context["step_summary"] = None
        if job and job.metadata:
            rag_result = job.metadata.get("rag_result") or {}
            doc_id = rag_result.get("document_id")
            if doc_id:
                from apps.knowledge.models import KnowledgeDocument
                try:
                    context["rag_doc"] = KnowledgeDocument.objects.get(id=doc_id)
                except KnowledgeDocument.DoesNotExist:
                    pass
            context["rag_timing"] = job.metadata.get("rag_timing")
            context["step_summary"] = rag_result.get("step_summary")
        return context


RAG_STATUS_MAP = {
    "queued": "pending",
    "running": "processing",
    "succeeded": "ready",
    "failed": "failed",
    "cancelled": "failed",
}


class DocumentStatusJsonView(LoginRequiredMixin, DetailView):
    model = DocumentReference

    def get_queryset(self):
        return DocumentReference.objects.filter(
            owner=self.request.user, kind=DocumentReference.Kind.KNOWLEDGE
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

        if job and job.metadata.get("rag_job_id") and rag_client.is_enabled():
            rag_data = rag_client.get_job(job.metadata["rag_job_id"])
            if rag_data:
                rag_status = rag_data.get("status")
                mapping_status = RAG_STATUS_MAP.get(rag_status)
                if mapping_status and mapping_status != processing_status:
                    rev.processing_status = mapping_status
                    rev.save(update_fields=["processing_status"])
                    processing_status = mapping_status

                step_summary = rag_data.get("step_summary") or {}
                ss_total = step_summary.get("total", 0)
                ss_done = step_summary.get("completed", 0)
                ss_failed = step_summary.get("failed", 0)
                if ss_total > 0:
                    progress_pct = round((ss_done + ss_failed) / ss_total * 100)
                current_step = step_summary.get("current_step")

                if rag_status == "succeeded":
                    job.status = IngestionJob.Status.SUCCEEDED
                    if rag_data.get("result"):
                        job.metadata["rag_result"] = rag_data["result"]
                    job.save(update_fields=["status", "metadata"])
                    job_status = job.status
                elif rag_status == "failed":
                    job.status = IngestionJob.Status.FAILED
                    job.error = rag_data.get("error", "")
                    job.save(update_fields=["status", "error"])
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
                docs_root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
                full_path = os.path.join(docs_root, rel_path)
                result = rag_client.ingest(full_path, ocr_mode=ocr_mode)
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


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        doc_ref = DocumentReference.objects.filter(id=pk, owner=request.user).first()
        if doc_ref:
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