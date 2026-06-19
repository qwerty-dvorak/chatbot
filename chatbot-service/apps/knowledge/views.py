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
        context["job"] = (
            IngestionJob.objects.filter(document_reference=self.object)
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
            rel_path = _save_doc_file(request.user.id, uploaded_file)
            document_title = title if title and len(uploaded_files) == 1 else uploaded_file.name

            blob, _ = ContentBlob.objects.get_or_create(
                content_hash=sha256,
                defaults={
                    "mime_type": mime,
                    "size_bytes": uploaded_file.size,
                    "object_key": rel_path,
                    "storage_status": ContentBlob.StorageStatus.STORED,
                },
            )
            revision = ArtifactRevision.objects.create(
                blob=blob,
                pipeline_fingerprint=sha256[:16],
                extracted_text="",
                processing_status=ArtifactRevision.ProcessingStatus.PENDING,
            )
            doc_ref = DocumentReference.objects.create(
                owner=request.user,
                artifact_revision=revision,
                title=document_title,
                kind=DocumentReference.Kind.KNOWLEDGE,
            )
            if rag_client.is_enabled():
                docs_root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
                full_path = os.path.join(docs_root, rel_path)
                rag_client.ingest(full_path, ocr_mode=ocr_mode, document_reference_id=str(doc_ref.id))
            else:
                IngestionJob.objects.create(document_reference=doc_ref)

        messages.success(request, f"{len(uploaded_files)} file(s) uploaded — processing started.")
        return redirect("knowledge:list")


class DocumentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        doc_ref = DocumentReference.objects.filter(id=pk, owner=request.user).first()
        if doc_ref:
            doc_ref.delete()
            messages.success(request, "Document deleted.")
        return redirect("knowledge:list")