import uuid

from django.db import models


class IngestionJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        "knowledge.Document", on_delete=models.CASCADE, related_name="ingestion_jobs"
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.QUEUED
    )
    attempts = models.IntegerField(default=0)
    error = models.TextField(default="")
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ingestion_jobs"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["document", "-created_at"]),
        ]

    def __str__(self):
        return f"Ingestion {self.document_id} - {self.status}"
