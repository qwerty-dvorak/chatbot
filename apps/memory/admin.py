from django.contrib import admin

from .models import Memory, MemorySettings


@admin.register(Memory)
class MemoryAdmin(admin.ModelAdmin):
    list_display = ("content", "user", "importance", "last_used_at", "created_at")
    list_filter = ("importance",)
    search_fields = ("content", "user__email")


@admin.register(MemorySettings)
class MemorySettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "is_enabled", "auto_save", "max_tokens")
    list_filter = ("is_enabled", "auto_save")
