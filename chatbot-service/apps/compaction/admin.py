from django.contrib import admin

from .models import ChatCompaction


@admin.register(ChatCompaction)
class ChatCompactionAdmin(admin.ModelAdmin):
    list_display = ("chat", "summary", "token_count", "model", "created_at")
    list_filter = ("model",)
