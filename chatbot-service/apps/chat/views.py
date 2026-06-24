import uuid

from django.contrib import messages as flash_messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView

from .forms import MessageForm
from .models import Chat, ChatShare, Message


class ChatListView(LoginRequiredMixin, ListView):
    model = Chat
    template_name = "chat/chat_list.html"
    context_object_name = "chats"

    def get_queryset(self):
        return Chat.objects.filter(user=self.request.user).order_by("-updated_at")


class ChatCreateView(LoginRequiredMixin, CreateView):
    model = Chat
    template_name = "chat/chat_new.html"
    fields = ["title"]

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.path = str(uuid.uuid4())[:8]
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("chat:detail", args=[self.object.id])


class ChatDetailView(LoginRequiredMixin, DetailView):
    model = Chat
    template_name = "chat/chat_detail.html"
    context_object_name = "chat"
    pk_url_kwarg = "chat_id"

    def get_queryset(self):
        return Chat.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.tools.models import ToolCall

        # Named chat_messages (not messages) to avoid shadowing Django's flash messages framework
        chat_messages = list(Message.objects.filter(chat=self.object).order_by("created_at"))

        # Attach tool calls + results to each assistant message in one query
        tc_qs = (
            ToolCall.objects
            .filter(chat=self.object)
            .prefetch_related("results")
            .order_by("sequence")
        )
        tc_by_msg: dict = {}
        for tc in tc_qs:
            tc_by_msg.setdefault(tc.message_id, []).append(tc)

        for msg in chat_messages:
            msg.tool_calls_data = tc_by_msg.get(msg.id, [])

        context["chat_messages"] = chat_messages
        context["form"] = MessageForm()
        context["pending_message"] = next(
            (m for m in reversed(chat_messages)
             if m.role == Message.Role.ASSISTANT
             and m.status in (Message.Status.PENDING, Message.Status.STREAMING)),
            None,
        )
        from apps.compaction.services import context_usage
        from apps.llm.lora import get_lora_adapters

        context["context_usage"] = context_usage(self.object)
        context["lora_adapters"] = get_lora_adapters()
        context["current_lora"] = (self.object.metadata or {}).get("lora_adapter", "")
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = MessageForm(request.POST, request.FILES)
        if form.is_valid():
            user_message = Message.objects.create(
                chat=self.object,
                role=Message.Role.USER,
                content=form.cleaned_data.get("content", ""),
                status=Message.Status.COMPLETED,
                metadata={"thinking_mode": form.cleaned_data.get("thinking_mode", False)},
            )
            Message.objects.create(
                chat=self.object,
                role=Message.Role.ASSISTANT,
                content="",
                status=Message.Status.PENDING,
                metadata={"thinking_mode": form.cleaned_data.get("thinking_mode", False)},
            )
            attachments = []
            knowledge_indices = set()
            raw = form.cleaned_data.get("knowledge_indices", "")
            if raw:
                try:
                    knowledge_indices = set(int(i) for i in raw.split(",") if i.strip())
                except (ValueError, TypeError):
                    pass
            for idx, file in enumerate(form.cleaned_data.get("attachment", [])):
                import hashlib
                sha256 = hashlib.sha256(file.read()).hexdigest()
                file.seek(0)
                from django.core.files.storage import default_storage
                path = default_storage.save(f"uploads/{file.name}", file)
                att = {
                    "file": path,
                    "original_filename": file.name,
                    "mime_type": file.content_type,
                    "size_bytes": file.size,
                    "sha256": sha256,
                }
                if idx in knowledge_indices:
                    doc_ref = self._ingest_attachment(request.user, file, path, sha256)
                    if doc_ref:
                        att["document_ref_id"] = str(doc_ref.id)
                attachments.append(att)
            if attachments:
                user_message.attachments = attachments
                user_message.save(update_fields=["attachments"])
            return redirect("chat:detail", chat_id=self.object.id)
        context = self.get_context_data()
        context["form"] = form
        return self.render_to_response(context)

    def _ingest_attachment(self, user, file, path, sha256):
        """Create a DocumentReference + trigger RAG ingestion for a chat attachment."""
        try:
            from apps.documents.models import ContentBlob, ArtifactRevision, DocumentReference
            from apps.ingestion.models import IngestionJob
            from apps.knowledge.rag_client import rag_client
            from apps.knowledge.views import _docs_storage, _save_doc_file
            from django.conf import settings
            from django.core.files.base import ContentFile

            from apps.ingestion.models import IngestionJob
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

            import os
            mime = file.content_type or "application/octet-stream"
            file.seek(0)
            raw = file.read()
            docs_storage = _docs_storage()
            rel_path = _save_doc_file(str(user.id), ContentFile(raw, file.name))

            if not doc_ref:
                blob, _ = ContentBlob.objects.get_or_create(
                    content_hash=sha256,
                    defaults={
                        "mime_type": mime,
                        "size_bytes": file.size,
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
                    title=file.name,
                    kind=DocumentReference.Kind.KNOWLEDGE,
                )
            else:
                revision = doc_ref.artifact_revision

            if rag_client.is_enabled():
                docs_root = getattr(settings, "DOCS_ROOT",
                                    os.path.join(settings.MEDIA_ROOT, "docs"))
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
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Failed to ingest attachment: %s", exc)
            return None


class ChatArchiveView(LoginRequiredMixin, View):
    """Toggle archived status. Accepts POST only (called from a form button)."""

    def post(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        chat.archived = not chat.archived
        chat.save(update_fields=["archived"])
        next_url = request.POST.get("next") or reverse("chat:list")
        return redirect(next_url)


class ChatLoraView(LoginRequiredMixin, View):
    """Update the LoRA adapter for a chat."""

    def post(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        lora = request.POST.get("lora_adapter", "").strip()
        meta = dict(chat.metadata)
        if lora:
            meta["lora_adapter"] = lora
        else:
            meta.pop("lora_adapter", None)
        chat.metadata = meta
        chat.save(update_fields=["metadata"])
        return redirect(request.POST.get("next") or reverse("chat:detail", args=[chat_id]))


class ChatCompactView(LoginRequiredMixin, View):
    def post(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        from apps.compaction.services import compact_chat

        try:
            compaction = compact_chat(chat)
            if compaction:
                flash_messages.success(
                    request,
                    f"Compacted older context into {compaction.token_count} summary tokens.",
                )
            else:
                flash_messages.info(
                    request,
                    "At least 10 uncompacted messages are required before compaction.",
                )
        except Exception as exc:
            flash_messages.error(request, f"Compaction failed: {exc}")
        return redirect("chat:detail", chat_id=chat.id)


class ChatShareView(LoginRequiredMixin, CreateView):
    model = ChatShare
    fields = []
    template_name = "chat/chat_share.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        chat = get_object_or_404(Chat, id=self.kwargs["chat_id"], user=self.request.user)
        context["chat"] = chat
        context["share"] = ChatShare.objects.filter(chat=chat, revoked=False).first()
        return context

    def form_valid(self, form):
        chat = get_object_or_404(Chat, id=self.kwargs["chat_id"], user=self.request.user)
        existing = ChatShare.objects.filter(chat=chat, revoked=False).first()
        if existing:
            existing.revoked = True
            existing.save(update_fields=["revoked"])
        form.instance.chat = chat
        form.instance.user = self.request.user
        form.instance.token = uuid.uuid4().hex
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("chat:share", args=[self.kwargs["chat_id"]])


class ChatStreamView(LoginRequiredMixin, View):
    def get(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        # Include STREAMING so a retry doesn't create a second orphan message
        pending_msg = Message.objects.filter(
            chat=chat,
            role=Message.Role.ASSISTANT,
            status__in=[Message.Status.PENDING, Message.Status.STREAMING],
        ).last()
        if not pending_msg:
            pending_msg = Message.objects.create(
                chat=chat,
                role=Message.Role.ASSISTANT,
                content="",
                status=Message.Status.PENDING,
            )
        from .streaming import stream_chat_response
        return stream_chat_response(pending_msg, request.user)


class ChatCancelStreamView(LoginRequiredMixin, View):
    def post(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        streaming_msg = Message.objects.filter(
            chat=chat,
            role=Message.Role.ASSISTANT,
            status__in=[Message.Status.PENDING, Message.Status.STREAMING],
        ).last()
        if not streaming_msg:
            return JsonResponse({"status": "not_found"}, status=404)
        streaming_msg.status = Message.Status.CANCELLED
        streaming_msg.save(update_fields=["status"])
        return JsonResponse({"status": "cancelled"})


class SharedChatView(TemplateView):
    template_name = "chat/chat_shared.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        share = get_object_or_404(ChatShare, token=kwargs["token"], revoked=False)
        context["share"] = share
        context["chat"] = share.chat
        context["chat_messages"] = Message.objects.filter(chat=share.chat).order_by("created_at")
        return context
