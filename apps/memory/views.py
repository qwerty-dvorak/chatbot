from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView, UpdateView

from .forms import MemorySettingsForm
from .models import Memory
from .services import get_memory_settings


class MemoryListView(LoginRequiredMixin, ListView):
    model = Memory
    template_name = "memory/memory_list.html"
    context_object_name = "memories"

    def get_queryset(self):
        return Memory.objects.filter(user=self.request.user).order_by("-importance", "-updated_at")


class MemorySettingsView(LoginRequiredMixin, TemplateView):
    template_name = "memory/memory_settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        settings = get_memory_settings(self.request.user)
        context["form"] = MemorySettingsForm(instance=settings)
        return context

    def post(self, request):
        settings = get_memory_settings(request.user)
        form = MemorySettingsForm(request.POST, instance=settings)
        if form.is_valid():
            form.save()
            return redirect("memory:settings")
        return self.render_to_response({"form": form})
