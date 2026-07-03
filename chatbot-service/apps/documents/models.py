import uuid

from django.conf import settings
from django.db import models


class ContentBlob(models.Model):
    class StorageStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        STORED = "stored", "Stored"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_hash = models.CharField(max_length=64, unique=True)
    mime_type = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    object_key = models.CharField(max_length=1024, blank=True, default="")
    storage_status = models.CharField(
        max_length=30, choices=StorageStatus.choices, default=StorageStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "content_blobs"
        indexes = [
            models.Index(fields=["content_hash"]),
            models.Index(fields=["storage_status"]),
        ]

    def __str__(self) -> str:
        return f"Blob {self.content_hash[:12]}... ({self.mime_type})"


class ArtifactRevision(models.Model):
    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    blob = models.ForeignKey(
        ContentBlob, on_delete=models.CASCADE, related_name="artifact_revisions"
    )
    pipeline_fingerprint = models.CharField(max_length=64, db_index=True)
    pipeline_version = models.CharField(max_length=50, default="1")
    extracted_text = models.TextField(default="", blank=True)
    summary = models.TextField(default="", blank=True)
    processing_status = models.CharField(
        max_length=30, choices=ProcessingStatus.choices, default=ProcessingStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "artifact_revisions"
        constraints = [
            models.UniqueConstraint(
                fields=["blob", "pipeline_fingerprint"],
                name="unique_blob_pipeline",
            )
        ]
        indexes = [
            models.Index(fields=["blob", "processing_status"]),
            models.Index(fields=["pipeline_fingerprint"]),
        ]

    def __str__(self) -> str:
        return f"Revision {self.pipeline_fingerprint[:12]}... ({self.processing_status})"


class DocumentReference(models.Model):
    class Kind(models.TextChoices):
        KNOWLEDGE = "knowledge", "Knowledge"
        CHAT_ATTACHMENT = "chat_attachment", "Chat Attachment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="document_references",
    )
    artifact_revision = models.ForeignKey(
        ArtifactRevision,
        on_delete=models.CASCADE,
        related_name="document_references",
    )
    title = models.CharField(max_length=512)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "document_references"
        indexes = [
            models.Index(fields=["owner", "kind", "-created_at"]),
            models.Index(fields=["owner", "-updated_at"]),
            models.Index(fields=["artifact_revision"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.kind})"


class DocumentGrant(models.Model):
    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"
        EDITOR = "editor", "Editor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_reference = models.ForeignKey(
        DocumentReference,
        on_delete=models.CASCADE,
        related_name="grants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="document_grants",
    )
    role = models.CharField(max_length=30, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_grants"
        constraints = [
            models.UniqueConstraint(
                fields=["document_reference", "user"],
                name="unique_document_grant",
            )
        ]
        indexes = [
            models.Index(fields=["document_reference"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} ({self.role}) on {self.document_reference_id}"


class EmbeddingSet(models.Model):
    class EmbeddingType(models.TextChoices):
        TEXT = "text", "Text"
        MULTIMODAL = "multimodal", "Multimodal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact_revision = models.ForeignKey(
        ArtifactRevision, on_delete=models.CASCADE, related_name="embedding_sets"
    )
    model_name = models.CharField(max_length=255)
    model_version = models.CharField(max_length=50, default="")
    embedding_type = models.CharField(max_length=30, choices=EmbeddingType.choices)
    dimensions = models.IntegerField(default=0)
    milvus_collection = models.CharField(max_length=255)
    milvus_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "embedding_sets"
        constraints = [
            models.UniqueConstraint(
                fields=["artifact_revision", "model_name", "embedding_type"],
                name="unique_revision_embedding",
            )
        ]
        indexes = [
            models.Index(fields=["artifact_revision"]),
            models.Index(fields=["model_name", "embedding_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.embedding_type} embed ({self.model_name})"
