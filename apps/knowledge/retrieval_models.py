import uuid

from django.db import models


class RetrievalRun(models.Model):
    class Strategy(models.TextChoices):
        VECTOR = "vector", "Vector"
        TEXT = "text", "Text"
        HYBRID = "hybrid", "Hybrid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="retrieval_runs")
    chat = models.ForeignKey("chat.Chat", on_delete=models.CASCADE, null=True, blank=True, related_name="retrieval_runs")
    message = models.ForeignKey("chat.Message", on_delete=models.CASCADE, null=True, blank=True, related_name="retrieval_runs")
    tool_call = models.ForeignKey("tools.ToolCall", on_delete=models.CASCADE, null=True, blank=True, related_name="retrieval_runs")
    query = models.TextField()
    query_embedding = models.JSONField(null=True, blank=True)
    strategy = models.CharField(max_length=30, choices=Strategy.choices, default=Strategy.HYBRID)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "retrieval_runs"
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["chat", "-created_at"]),
            models.Index(fields=["tool_call"]),
        ]

    def __str__(self):
        return f"Retrieval {self.strategy}: {self.query[:50]}"


class RetrievalHit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(RetrievalRun, on_delete=models.CASCADE, related_name="hits")
    chunk = models.ForeignKey("knowledge.DocumentChunk", on_delete=models.CASCADE, related_name="retrieval_hits")
    rank = models.IntegerField()
    score = models.FloatField(default=0)
    source_title = models.CharField(max_length=512)
    source_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "retrieval_hits"
        constraints = [
            models.UniqueConstraint(fields=["run", "chunk"], name="unique_retrieval_hit")
        ]
        indexes = [
            models.Index(fields=["run", "rank"]),
            models.Index(fields=["chunk"]),
        ]

    def __str__(self):
        return f"Hit #{self.rank} ({self.score:.3f})"
