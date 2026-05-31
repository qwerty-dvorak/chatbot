from django.conf import settings as django_settings
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from .forms import RegistrationForm
from .models import SecurityLog


class RegisterView(CreateView):
    form_class = RegistrationForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("chat:list")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.object
        login(self.request, user)
        SecurityLog.objects.create(
            user=user,
            event="registration",
            method="password",
            ip_address=self.request.META.get("REMOTE_ADDR"),
            user_agent=self.request.META.get("HTTP_USER_AGENT"),
        )
        return response


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = "settings/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from apps.memory.services import get_memory_settings
        from apps.memory.forms import MemorySettingsForm
        from apps.tools.models import ToolDefinition

        mem_settings = get_memory_settings(self.request.user)
        context["mem_form"] = MemorySettingsForm(instance=mem_settings)
        context["tools"] = ToolDefinition.objects.all().order_by("name")
        context["sys"] = {
            "chat_base_url": django_settings.CHAT_BASE_URL,
            "chat_model": django_settings.CHAT_MODEL,
            "embedding_base_url": django_settings.EMBEDDING_BASE_URL,
            "text_embedding_model": django_settings.TEXT_EMBEDDING_MODEL,
            "rag_enabled": django_settings.RAG_ENABLED,
            "tool_calls_enabled": django_settings.TOOL_CALLS_ENABLED,
            "docs_root": getattr(django_settings, "DOCS_ROOT", "—"),
        }
        return context

    def post(self, request):
        action = request.POST.get("action")

        if action == "memory":
            from apps.memory.services import get_memory_settings
            from apps.memory.forms import MemorySettingsForm
            mem_settings = get_memory_settings(request.user)
            form = MemorySettingsForm(request.POST, instance=mem_settings)
            if form.is_valid():
                form.save()

        elif action == "toggle_tool":
            from apps.tools.models import ToolDefinition
            tool_id = request.POST.get("tool_id")
            ToolDefinition.objects.filter(id=tool_id).update(
                is_enabled=not ToolDefinition.objects.get(id=tool_id).is_enabled
            )

        return redirect("settings")
