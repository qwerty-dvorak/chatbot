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
                # Capture embedding info from job result
                if rag_status == "succeeded" and rag_result.get("result"):
                    meta = self.object.metadata
                    if "embedding_info" not in meta:
                        results_list = rag_result.get("result", {}).get("results", [])
                        if results_list:
                            meta["embedding_info"] = results_list[0].get("embedding_info", {})
                        elif isinstance(rag_result.get("result"), dict):
                            meta["embedding_info"] = rag_result["result"].get("embedding_info", {})
                        self.object.metadata = meta
                        self.object.save(update_fields=["metadata"])
        return context


class DocumentUploadView(LoginRequiredMixin, View):
    template_name = "knowledge/upload.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        title = request.POST.get("title", "").strip()
        uploaded_file = request.FILES.get("file")
        visibility = request.POST.get("visibility", request.GET.get("visibility", "private"))
        if visibility not in ("private", "shared", "global"):
            visibility = "private"

        if not uploaded_file:
            return render(request, self.template_name,
                          {"error": "Please select a file."})

        mime = uploaded_file.content_type or "application/octet-stream"
        if mime not in ALLOWED_MIME:
            return render(request, self.template_name,
                          {"error": f"File type '{mime}' is not supported. "
                                    "Allowed: PDF, images, plain text, Markdown, CSV."})
        if uploaded_file.size > 50 * 1024 * 1024:
            return render(request, self.template_name,
                          {"error": "File must be under 50 MB."})

        sha256 = hashlib.sha256(uploaded_file.read()).hexdigest()
        uploaded_file.seek(0)
        rel_path = _save_doc_file(request.user.id, uploaded_file)

        if not title:
            title = uploaded_file.name

        source, _ = KnowledgeSource.objects.get_or_create(
            owner=request.user,
            name=f"Uploads – {request.user.email}",
            defaults={"source_type": "upload", "visibility": visibility},
        )
        if source.visibility != visibility:
            source.visibility = visibility
            source.save(update_fields=["visibility"])

        rag_tier = "global" if visibility == "global" else "slow" if visibility == "shared" else "instant"
        doc = Document.objects.create(
            source=source,
            owner=request.user,
            title=title,
            original_filename=uploaded_file.name,
            mime_type=mime,
            file=rel_path,
            sha256=sha256,
            status=Document.Status.PENDING,
        )

        if rag_client.is_enabled():
            docs_root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
            full_path = os.path.join(docs_root, rel_path)
            job = rag_client.ingest(full_path, tier=rag_tier)
            if job:
                doc.metadata["rag_job_id"] = job.get("id")
                doc.metadata["rag_api_url"] = rag_client.base_url
                doc.save(update_fields=["metadata"])
                msg = f"'{title}' uploaded — RAG API processing started (tier={rag_tier})."
            else:
                msg = f"'{title}' uploaded — RAG API unavailable, local ingestion queued."
                IngestionJob.objects.create(document=doc)
        else:
            IngestionJob.objects.create(document=doc)
            msg = f"'{title}' uploaded — processing started."

        messages.success(request, msg)
        return redirect("knowledge:list")


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        doc = Document.objects.filter(id=pk, owner=request.user).first()
        if doc:
            doc.delete()
            messages.success(request, "Document deleted.")
        return redirect("knowledge:list")
