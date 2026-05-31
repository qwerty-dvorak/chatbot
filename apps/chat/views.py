import uuid

from django.contrib.auth.mixins import LoginRequiredMixin
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
        # Named chat_messages (not messages) to avoid shadowing Django's flash messages framework
        chat_messages = Message.objects.filter(chat=self.object).order_by("created_at")
        context["chat_messages"] = chat_messages
        context["form"] = MessageForm()
        context["user_chats"] = (
            Chat.objects.filter(user=self.request.user)
            .order_by("-updated_at")[:40]
        )
        context["pending_message"] = chat_messages.filter(
            role=Message.Role.ASSISTANT,
            status__in=[Message.Status.PENDING, Message.Status.STREAMING],
        ).last()
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
            )
            Message.objects.create(
                chat=self.object,
                role=Message.Role.ASSISTANT,
                content="",
                status=Message.Status.PENDING,
            )
            if form.cleaned_data.get("attachment"):
                file = form.cleaned_data["attachment"]
                import hashlib
                sha256 = hashlib.sha256(file.read()).hexdigest()
                file.seek(0)
                from django.core.files.storage import default_storage
                path = default_storage.save(f"uploads/{file.name}", file)
                user_message.attachments = [{
                    "file": path,
                    "original_filename": file.name,
                    "mime_type": file.content_type,
                    "size_bytes": file.size,
                    "sha256": sha256,
                }]
                user_message.save(update_fields=["attachments"])
            return redirect("chat:detail", chat_id=self.object.id)
        context = self.get_context_data()
        context["form"] = form
        return self.render_to_response(context)


class ChatArchiveView(LoginRequiredMixin, View):
    """Toggle archived status. Accepts POST only (called from a form button)."""

    def post(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id, user=request.user)
        chat.archived = not chat.archived
        chat.save(update_fields=["archived"])
        next_url = request.POST.get("next") or reverse("chat:list")
        return redirect(next_url)


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
        pending_msg = Message.objects.filter(
            chat=chat, role=Message.Role.ASSISTANT, status=Message.Status.PENDING
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


class SharedChatView(TemplateView):
    template_name = "chat/chat_shared.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        share = get_object_or_404(ChatShare, token=kwargs["token"], revoked=False)
        context["share"] = share
        context["chat"] = share.chat
        context["chat_messages"] = Message.objects.filter(chat=share.chat).order_by("created_at")
        return context
