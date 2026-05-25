import hashlib
import uuid

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.storage import default_storage
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView

from .models import Document, DocumentChunk, KnowledgeSource


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
        context["chunks"] = DocumentChunk.objects.filter(document=self.object).order_by("chunk_index")
        return context


class DocumentUploadView(LoginRequiredMixin, CreateView):
    model = Document
    template_name = "knowledge/upload.html"
    fields = ["title"]

    def form_valid(self, form):
        form.instance.owner = self.request.user
        uploaded_file = self.request.FILES.get("file")
        if uploaded_file:
            sha256 = hashlib.sha256(uploaded_file.read()).hexdigest()
            uploaded_file.seek(0)
            path = default_storage.save(f"uploads/{uploaded_file.name}", uploaded_file)
            form.instance.file = path
            form.instance.original_filename = uploaded_file.name
            form.instance.sha256 = sha256
            form.instance.mime_type = uploaded_file.content_type or "application/octet-stream"
            form.instance.status = Document.Status.PENDING
        else:
            form.instance.mime_type = "application/octet-stream"
        if not form.instance.title:
            form.instance.title = uploaded_file.name if uploaded_file else "Untitled"
        source, _ = KnowledgeSource.objects.get_or_create(
            owner=self.request.user,
            name=f"Uploads - {self.request.user.email}",
            defaults={"source_type": "upload", "visibility": "private"},
        )
        form.instance.source = source
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("knowledge:list")
