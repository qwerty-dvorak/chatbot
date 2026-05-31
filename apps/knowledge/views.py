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
        return context


class DocumentUploadView(LoginRequiredMixin, View):
    template_name = "knowledge/upload.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        title = request.POST.get("title", "").strip()
        uploaded_file = request.FILES.get("file")

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
            defaults={"source_type": "upload", "visibility": "private"},
        )
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
        IngestionJob.objects.create(document=doc)

        messages.success(request, f"'{title}' uploaded — processing started.")
        return redirect("knowledge:list")


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        doc = Document.objects.filter(id=pk, owner=request.user).first()
        if doc:
            doc.delete()
            messages.success(request, "Document deleted.")
        return redirect("knowledge:list")
