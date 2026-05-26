import uuid

from django.conf import settings
from django.db import models


class Memory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memories"
    )
    content = models.TextField()
    tags = models.JSONField(default=list, blank=True)
    importance = models.IntegerField(default=1)
    last_used_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "memories"
        indexes = [
            models.Index(fields=["user", "importance", "-last_used_at"]),
            models.Index(fields=["user", "-updated_at"]),
        ]

    def __str__(self):
        return self.content[:60]

    def get_milvus_id(self):
        return str(self.id)


class MemorySettings(models.Model):
    class AutoSaveFilter(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memory_settings"
    )
    is_enabled = models.BooleanField(default=True)
    max_tokens = models.IntegerField(default=2000)
    auto_save = models.BooleanField(default=True)
    auto_save_filter = models.CharField(
        max_length=30, choices=AutoSaveFilter.choices, default=AutoSaveFilter.MEDIUM
    )
    allow_tool_updates = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "memory_settings"

    def __str__(self):
        return f"Memory settings for {self.user.email}"
