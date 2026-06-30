import uuid

from django.db import models


class IngestionJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_reference = models.ForeignKey(
        "documents.DocumentReference",
        on_delete=models.CASCADE,
        related_name="ingestion_jobs",
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.QUEUED
    )
    attempts = models.IntegerField(default=0)
    error = models.TextField(default="")
    metadata = models.JSONField(default=dict, blank=True)
    queue_order = models.IntegerField(default=0, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ingestion_jobs"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["document_reference", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Ingestion {self.document_reference_id} - {self.status}"


class IngestionStepAttempt(models.Model):
    class StepType(models.TextChoices):
        EXTRACT = "extract", "Extract"
        OCR = "ocr", "OCR"
        CHUNK = "chunk", "Chunk"
        SUMMARIZE = "summarize", "Summarize"
        HYPOTHETICAL_QUESTIONS = "hypothetical_questions", "Hypothetical Questions"
        EMBED_TEXT = "embed_text", "Embed Text"
        EMBED_MULTIMODAL = "embed_multimodal", "Embed Multimodal"
        INDEX = "index", "Index"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        IngestionJob, on_delete=models.CASCADE, related_name="step_attempts"
    )
    step_type = models.CharField(max_length=50, choices=StepType.choices)
    attempt_number = models.IntegerField(default=1)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.QUEUED
    )
    error = models.TextField(default="", blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ingestion_step_attempts"
        indexes = [
            models.Index(fields=["job", "step_type"]),
            models.Index(fields=["job", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "step_type", "attempt_number"],
                name="unique_job_step_attempt",
            )
        ]

    def __str__(self) -> str:
        return f"{self.step_type} attempt {self.attempt_number} ({self.status})"
