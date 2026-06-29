import json
import logging
import uuid

from django.contrib import messages as flash_messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView

from .forms import MessageForm
from .models import Chat, ChatShare, Message, MessageEdit

logger = logging.getLogger(__name__)


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

        inflight = any(
            m.role == Message.Role.ASSISTANT
            and m.status in (Message.Status.PENDING, Message.Status.STREAMING)
            for m in chat_messages
        )
        last_user_message = next(
            (m for m in reversed(chat_messages) if m.role == Message.Role.USER),
            None,
        )

        for msg in chat_messages:
            msg.tool_calls_data = tc_by_msg.get(msg.id, [])
            msg.can_edit = (
                last_user_message is not None
                and msg.id == last_user_message.id
                and not inflight
                and not self.object.archived
            )

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
                author=request.user,
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
                    doc_ref = ingest_attachment(request.user, file, path, sha256)
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

def ingest_attachment(user, file, file_storage_path, sha256):
    """Create a DocumentReference + trigger RAG ingestion for a chat attachment."""
    try:
        from apps.documents.models import ContentBlob, ArtifactRevision, DocumentReference
        from apps.ingestion.models import IngestionJob
        from apps.knowledge.rag_client import rag_client
        from apps.knowledge.views import _docs_storage, _save_doc_file
        from django.conf import settings
        from django.core.files.base import ContentFile

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
                    logger.info("Reusing existing doc_ref %s for user %s, sha256=%s", doc_ref.id, user.id, sha256[:12])
                    return doc_ref

        import os
        mime = file.content_type or "application/octet-stream"
        file.seek(0)
        raw = file.read()
        rel_path = _save_doc_file(str(user.id), ContentFile(raw, file.name))
        logger.info("Saved attachment file to %s for user %s", rel_path, user.id)

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
            logger.info("Created new doc_ref %s for file %s", doc_ref.id, file.name)
        else:
            revision = doc_ref.artifact_revision
            logger.info("Reusing blob for doc_ref %s, file %s", doc_ref.id, file.name)

        if rag_client.is_enabled():
            docs_root = getattr(settings, "DOCS_ROOT",
                                os.path.join(settings.MEDIA_ROOT, "docs"))
            full_path = os.path.join(docs_root, rel_path)
            logger.info("Calling rag_client.ingest for %s", full_path)
            ingest_result = rag_client.ingest(full_path)
            job = None
            if ingest_result and ingest_result.get("id"):
                logger.info("RAG ingest job %s started for doc_ref %s", ingest_result["id"], doc_ref.id)
                job = rag_client.poll_job(ingest_result["id"], max_retries=30, interval=0.5)
                IngestionJob.objects.create(
                    document_reference=doc_ref,
                    metadata={"rag_job_id": ingest_result["id"], "status": (job or {}).get("status", "unknown")},
                )
            if job and job.get("status") in ("succeeded", "completed"):
                revision.processing_status = ArtifactRevision.ProcessingStatus.READY
                revision.save(update_fields=["processing_status"])
                logger.info("RAG ingest completed for doc_ref %s", doc_ref.id)
            else:
                job_status = (job or {}).get("status", "no-job")
                logger.warning("RAG ingest did not complete for doc_ref %s — status=%s", doc_ref.id, job_status)
        else:
            IngestionJob.objects.create(document_reference=doc_ref)
            logger.info("Queued local ingestion job for doc_ref %s", doc_ref.id)
        return doc_ref
    except Exception as exc:
        logger.exception("Failed to ingest attachment: %s", exc)
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
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            import json as json_mod
            data = json_mod.loads(request.body) if request.body else {}
            lora = data.get("lora_adapter", "").strip()
        else:
            lora = request.POST.get("lora_adapter", "").strip()
        meta = dict(chat.metadata)
        if lora:
            meta["lora_adapter"] = lora
        else:
            meta.pop("lora_adapter", None)
        chat.metadata = meta
        chat.save(update_fields=["metadata"])
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"status": "ok", "lora_adapter": lora or None})
        return redirect(request.POST.get("next") or reverse("chat:detail", args=[chat_id]))


class ChatMessageEditView(LoginRequiredMixin, View):
    """Rewrite the last user turn and queue a fresh assistant response."""

    def post(self, request, chat_id, msg_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        if chat.archived:
            return JsonResponse({"error": "Archived chats cannot be edited."}, status=409)

        try:
            if request.headers.get("Content-Type", "").startswith("application/json"):
                data = json.loads(request.body or "{}")
                content = data.get("content", "")
            else:
                content = request.POST.get("content", "")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(content, str):
            return JsonResponse({"error": "Message content must be a string."}, status=400)

        with transaction.atomic():
            message = get_object_or_404(
                Message.objects.select_for_update(),
                id=msg_id,
                chat=chat,
                role=Message.Role.USER,
            )
            last_user = (
                Message.objects
                .select_for_update()
                .filter(chat=chat, role=Message.Role.USER)
                .order_by("-created_at")
                .first()
            )
            if not last_user or last_user.id != message.id:
                return JsonResponse(
                    {"error": "Only the latest user message can be edited."},
                    status=409,
                )

            if Message.objects.filter(
                chat=chat,
                role=Message.Role.ASSISTANT,
                status__in=[Message.Status.PENDING, Message.Status.STREAMING],
            ).exists():
                return JsonResponse(
                    {"error": "Wait for the current response to finish before editing."},
                    status=409,
                )

            if not content.strip() and not message.attachments:
                return JsonResponse(
                    {"error": "Message content or attachment is required."},
                    status=400,
                )

            if content == message.content:
                return JsonResponse({
                    "status": "unchanged",
                    "message_id": str(message.id),
                    "content": message.content,
                })

            superseded = list(
                Message.objects
                .filter(chat=chat, created_at__gt=message.created_at)
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            previous_metadata = dict(message.metadata or {})
            new_metadata = dict(previous_metadata)
            new_metadata.pop("rag_search_log", None)
            new_metadata["edited"] = True
            new_metadata["last_edit_at"] = timezone.now().isoformat()

            edit = MessageEdit.objects.create(
                message=message,
                editor=request.user,
                previous_content=message.content,
                new_content=content,
                previous_metadata=previous_metadata,
                new_metadata=new_metadata,
                superseded_message_ids=[str(item) for item in superseded],
            )

            if superseded:
                Message.objects.filter(id__in=superseded).delete()

            now = timezone.now()
            message.content = content
            message.metadata = new_metadata
            message.edit_count = message.edit_count + 1
            message.edited_at = now
            message.status = Message.Status.COMPLETED
            message.save(update_fields=["content", "metadata", "edit_count", "edited_at", "status"])

            assistant = Message.objects.create(
                chat=chat,
                role=Message.Role.ASSISTANT,
                content="",
                status=Message.Status.PENDING,
                metadata={
                    "thinking_mode": bool(new_metadata.get("thinking_mode")),
                    "regenerated_from_edit_id": str(edit.id),
                },
            )
            chat.updated_at = now
            chat.save(update_fields=["updated_at"])

        return JsonResponse({
            "status": "edited",
            "message_id": str(message.id),
            "assistant_message_id": str(assistant.id),
            "content": message.content,
            "edit_count": message.edit_count,
            "edited_at": message.edited_at.isoformat() if message.edited_at else None,
            "stream_url": reverse("chat:stream", args=[chat.id]),
        })


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


class ChatShareJsonView(LoginRequiredMixin, View):
    """JSON API for creating/revoking/checking share links."""

    def get(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        share = ChatShare.objects.filter(chat=chat, revoked=False).first()
        if not share:
            return JsonResponse({"active": False, "url": None})
        return JsonResponse({
            "active": True,
            "token": share.token,
            "url": request.build_absolute_uri(reverse("chat-shared", args=[share.token])),
            "created_at": share.created_at.isoformat() if share.created_at else None,
        })

    def post(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        existing = ChatShare.objects.filter(chat=chat, revoked=False).first()
        if existing:
            existing.revoked = True
            existing.save(update_fields=["revoked"])
        share = ChatShare.objects.create(
            chat=chat,
            user=request.user,
            token=uuid.uuid4().hex,
        )
        return JsonResponse({
            "active": True,
            "token": share.token,
            "url": request.build_absolute_uri(reverse("chat-shared", args=[share.token])),
            "created_at": share.created_at.isoformat() if share.created_at else None,
        })

    def delete(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        share = ChatShare.objects.filter(chat=chat, revoked=False).first()
        if share:
            share.revoked = True
            share.save(update_fields=["revoked"])
        return JsonResponse({"active": False})


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


class ChatAttachmentView(LoginRequiredMixin, View):
    """Serve an attachment file from a user message."""

    def get(self, request, chat_id, msg_id, filename):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        msg = get_object_or_404(Message, id=msg_id, chat=chat)
        if not msg.attachments:
            return JsonResponse({"error": "no attachments"}, status=404)
        att = None
        for a in msg.attachments:
            if a.get("original_filename") == filename or a.get("file", "").endswith(filename):
                att = a
                break
        if not att:
            return JsonResponse({"error": "file not found"}, status=404)
        file_path = att["file"]
        from django.core.files.storage import default_storage
        if not default_storage.exists(file_path):
            return JsonResponse({"error": "file not found on disk"}, status=404)
        from django.http import FileResponse
        import mimetypes
        mime = att.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        disposition = "inline" if mime.startswith("image/") else "attachment"
        response = FileResponse(default_storage.open(file_path, "rb"), content_type=mime)
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response["Content-Length"] = att.get("size_bytes", 0)
        return response


class ChatAttachmentIngestView(LoginRequiredMixin, View):
    """Toggle (ingest) an existing message attachment into the knowledge base."""

    def post(self, request, chat_id, msg_id, index):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        msg = get_object_or_404(Message, id=msg_id, chat=chat)
        if not msg.attachments or index < 0 or index >= len(msg.attachments):
            return JsonResponse({"error": "invalid attachment index"}, status=400)
        attachments = list(msg.attachments)
        att = attachments[index]
        if att.get("document_ref_id"):
            return JsonResponse({"status": "already_ingested", "document_ref_id": att["document_ref_id"]})
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        file_path = att["file"]
        if not default_storage.exists(file_path):
            return JsonResponse({"error": "file not found"}, status=404)
        raw = default_storage.open(file_path, "rb").read()
        fake_file = ContentFile(raw, name=att["original_filename"])
        fake_file.content_type = att.get("mime_type", "application/octet-stream")
        import traceback
        try:
            doc_ref = ingest_attachment(request.user, fake_file, file_path, att.get("sha256", ""))
        except Exception as exc:
            return JsonResponse({"error": f"Ingestion failed: {exc}"}, status=500)
        if doc_ref:
            att["document_ref_id"] = str(doc_ref.id)
            msg.attachments = attachments
            msg.save(update_fields=["attachments"])
            return JsonResponse({"status": "ingested", "document_ref_id": str(doc_ref.id)})
        return JsonResponse({"error": "Ingestion failed — check server logs"}, status=500)


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


class SharedChatContinueView(LoginRequiredMixin, View):
    """Fork a shared chat into a new chat for the logged-in user."""

    def post(self, request, token):
        share = get_object_or_404(ChatShare, token=token, revoked=False)
        original = share.chat
        import uuid as uuid_mod
        new_chat = Chat.objects.create(
            user=request.user,
            title=original.title or "Continued chat",
            path=uuid_mod.uuid4().hex[:16],
            metadata=original.metadata or {},
        )
        for msg in Message.objects.filter(chat=original).order_by("created_at"):
            Message.objects.create(
                chat=new_chat,
                role=msg.role,
                content=msg.content,
                attachments=msg.attachments,
                tool_invocations=msg.tool_invocations,
                metadata=msg.metadata,
                status=msg.status,
                created_at=msg.created_at,
            )
        return redirect("chat:detail", chat_id=new_chat.id)
