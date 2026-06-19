import hashlib
import os
import uuid
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import FileSystemStorage
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import DetailView, ListView

from apps.ingestion.models import IngestionJob

from .models import Document, DocumentChunk, KnowledgeSource
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
    """Store at DOCS_ROOT/<user_id>/<date>/knowledge/<name>. Returns relative path."""
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
    model = Document
    template_name = "knowledge/document_list.html"
    context_object_name = "documents"

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user).order_by("-created_at")


class DocumentDetailView(LoginRequiredMixin, DetailView):
    model = Document
    template_name = "knowledge/document_detail.html"
    context_object_name = "document"

    def get_queryset(self):
        return Document.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["chunks"] = DocumentChunk.objects.filter(
            document=self.object
        ).order_by("chunk_index")
        context["job"] = (
            IngestionJob.objects.filter(document=self.object)
            .order_by("-created_at")
            .first()
        )
        rag_job_id = self.object.metadata.get("rag_job_id")
        if rag_job_id and rag_client.is_enabled():
            rag_result = rag_client.get_job(rag_job_id)
            if rag_result:
                rag_status = rag_result.get("status", "")
                context["rag_job"] = rag_result
                if rag_status == "succeeded" and self.object.status == "pending":
                    self.object.status = "ready"
                    self.object.save(update_fields=["status"])
                # Capture embedding info and timing from job result
                if rag_status == "succeeded" and rag_result.get("result"):
                    meta = self.object.metadata
                    results_list = rag_result.get("result", {}).get("results", [])
                    first_result = results_list[0] if results_list else (
                        rag_result.get("result") if isinstance(rag_result.get("result"), dict) else {}
                    )
                    if "embedding_info" not in meta:
                        meta["embedding_info"] = first_result.get("embedding_info", {})
                    # Capture per-step timing if available
                    if "timing" not in meta and first_result.get("timing"):
                        meta["timing"] = first_result["timing"]
                    # Capture files_processed count
                    files_proc = rag_result.get("result", {}).get("files_processed")
                    if files_proc is not None:
                        meta["files_processed"] = files_proc
                    if meta != self.object.metadata:
                        self.object.metadata = meta
                        self.object.save(update_fields=["metadata"])
        return context


class DocumentUploadView(LoginRequiredMixin, View):
    template_name = "knowledge/upload.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        title = request.POST.get("title", "").strip()
        uploaded_files = request.FILES.getlist("files") or request.FILES.getlist("file")
        visibility = request.POST.get("visibility", request.GET.get("visibility", "private"))
        ocr_mode = request.POST.get("ocr_mode", "paddleocr")
        if visibility not in ("private", "shared", "global"):
            visibility = "private"
        if ocr_mode not in ("none", "basic", "paddleocr"):
            ocr_mode = "paddleocr"

        if not uploaded_files:
            return render(request, self.template_name,
                          {"error": "Please select a file."})

        for uploaded_file in uploaded_files:
            mime = uploaded_file.content_type or "application/octet-stream"
            if mime not in ALLOWED_MIME:
                return render(request, self.template_name,
                              {"error": f"File type '{mime}' is not supported: {uploaded_file.name}."})
            if uploaded_file.size > 50 * 1024 * 1024:
                return render(request, self.template_name,
                              {"error": f"File must be under 50 MB: {uploaded_file.name}."})

        source, _ = KnowledgeSource.objects.get_or_create(
            owner=request.user,
            name=f"Uploads – {request.user.email}",
            defaults={"source_type": "upload", "visibility": visibility},
        )
        if source.visibility != visibility:
            source.visibility = visibility
            source.save(update_fields=["visibility"])

        rag_tier = "global" if visibility == "global" else "slow" if visibility == "shared" else "instant"
        for uploaded_file in uploaded_files:
            mime = uploaded_file.content_type or "application/octet-stream"
            sha256 = hashlib.sha256(uploaded_file.read()).hexdigest()
            uploaded_file.seek(0)
            rel_path = _save_doc_file(request.user.id, uploaded_file)
            document_title = title if title and len(uploaded_files) == 1 else uploaded_file.name
            doc = Document.objects.create(
                source=source, owner=request.user, title=document_title,
                original_filename=uploaded_file.name, mime_type=mime, file=rel_path,
                sha256=sha256, status=Document.Status.PENDING, ocr_mode=ocr_mode,
            )

            if rag_client.is_enabled():
                docs_root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
                full_path = os.path.join(docs_root, rel_path)
                job = rag_client.ingest(full_path, tier=rag_tier, ocr_mode=ocr_mode, document_id=str(doc.id))
                if job:
                    doc.metadata.update({"rag_job_id": job.get("id"), "rag_api_url": rag_client.base_url})
                    doc.save(update_fields=["metadata"])
                else:
                    IngestionJob.objects.create(document=doc)
            else:
                IngestionJob.objects.create(document=doc)

        messages.success(request, f"{len(uploaded_files)} file(s) uploaded — processing started.")
        return redirect("knowledge:list")


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        doc = Document.objects.filter(id=pk, owner=request.user).first()
        if doc:
            doc.delete()
            messages.success(request, "Document deleted.")
        return redirect("knowledge:list")
