import uuid

from django.db import models


class ChatCompaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey("chat.Chat", on_delete=models.CASCADE, related_name="compactions")
    from_message = models.ForeignKey(
        "chat.Message", on_delete=models.CASCADE, related_name="compactions_from"
    )
    to_message = models.ForeignKey(
        "chat.Message", on_delete=models.CASCADE, related_name="compactions_to"
    )
    summary = models.TextField()
    facts = models.JSONField(default=list, blank=True)
    open_questions = models.JSONField(default=list, blank=True)
    token_count = models.IntegerField(default=0)
    model = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_compactions"
        indexes = [
            models.Index(fields=["chat", "-created_at"]),
            models.Index(fields=["chat", "from_message", "to_message"]),
        ]

    def __str__(self):
        return f"Compaction for {self.chat_id} @ {self.created_at}"
