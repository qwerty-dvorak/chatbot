import uuid

from django.conf import settings
from django.db import models


class TokenUsage(models.Model):
    class Operation(models.TextChoices):
        CHAT = "chat", "Chat"
        EMBEDDING = "embedding", "Embedding"
        VISION_ANALYSIS = "vision_analysis", "Vision Analysis"
        COMPACTION = "compaction", "Compaction"
        MEMORY = "memory", "Memory"
        TOOL_CALL = "tool_call", "Tool Call"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="token_usage"
    )
    chat = models.ForeignKey(
        "chat.Chat", on_delete=models.CASCADE, null=True, blank=True, related_name="token_usage"
    )
    message = models.ForeignKey(
        "chat.Message", on_delete=models.CASCADE, null=True, blank=True, related_name="token_usage"
    )
    tool_call = models.ForeignKey(
        "tools.ToolCall", on_delete=models.CASCADE, null=True, blank=True, related_name="token_usage"
    )
    operation = models.CharField(max_length=50, choices=Operation.choices)
    provider = models.CharField(max_length=100, default="litellm")
    model = models.CharField(max_length=255, blank=True, null=True)
    request_id = models.CharField(max_length=255, blank=True, null=True)
    step_index = models.IntegerField(null=True, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "token_usage"
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["chat", "-created_at"]),
            models.Index(fields=["model"]),
            models.Index(fields=["operation"]),
            models.Index(fields=["tool_call"]),
        ]

    def __str__(self):
        return f"{self.operation}: {self.total_tokens}tokens"
