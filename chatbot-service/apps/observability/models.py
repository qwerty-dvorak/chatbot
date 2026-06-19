import uuid

from django.db import models


class ExecutionTrace(models.Model):
    class TraceType(models.TextChoices):
        INGESTION = "ingestion", "Ingestion"
        TURN = "turn", "Turn"
        RETRIEVAL = "retrieval", "Retrieval"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace_type = models.CharField(max_length=30, choices=TraceType.choices)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "execution_traces"
        indexes = [
            models.Index(fields=["trace_type", "-started_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"Trace {self.trace_type} ({self.status})"


class ExecutionSpan(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        ExecutionTrace, on_delete=models.CASCADE, related_name="spans"
    )
    parent_span = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="child_spans"
    )
    operation = models.CharField(max_length=100)
    provider = models.CharField(max_length=100, blank=True, default="")
    model = models.CharField(max_length=255, blank=True, default="")
    lora_adapter = models.CharField(max_length=255, blank=True, default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    time_to_first_token_ms = models.IntegerField(null=True, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    retry_count = models.IntegerField(default=0)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.SUCCESS
    )
    error_code = models.CharField(max_length=100, blank=True, default="")
    dimensions = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "execution_spans"
        indexes = [
            models.Index(fields=["trace", "operation"]),
            models.Index(fields=["trace", "parent_span"]),
            models.Index(fields=["trace", "-started_at"]),
            models.Index(fields=["operation", "status"]),
        ]

    def __str__(self):
        return f"Span {self.operation} ({self.status})"
