from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView, ListView

from .models import ToolCall, ToolDefinition


class ToolListView(LoginRequiredMixin, ListView):
    model = ToolDefinition
    template_name = "tools/tool_list.html"
    context_object_name = "tools"

    def get_queryset(self):
        return ToolDefinition.objects.filter(is_enabled=True)


class ToolCallDetailView(LoginRequiredMixin, DetailView):
    model = ToolCall
    template_name = "tools/tool_call_detail.html"
    context_object_name = "tool_call"

    def get_queryset(self):
        return ToolCall.objects.filter(user=self.request.user)
