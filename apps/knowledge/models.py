import uuid

from django.conf import settings
from django.db import models


class KnowledgeSource(models.Model):
    class SourceType(models.TextChoices):
        UPLOAD = "upload", "Upload"
        FOLDER = "folder", "Folder"
        MANUAL = "manual", "Manual"
        API = "api", "API"

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        SHARED = "shared", "Shared"
        GLOBAL = "global", "Global"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="knowledge_sources")
    name = models.CharField(max_length=255)
    source_type = models.CharField(max_length=50, choices=SourceType.choices, default=SourceType.UPLOAD)
    visibility = models.CharField(max_length=30, choices=Visibility.choices, default="private")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "knowledge_sources"
        indexes = [
            models.Index(fields=["owner", "source_type"]),
            models.Index(fields=["visibility"]),
        ]

    def __str__(self):
        return self.name


class Document(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(KnowledgeSource, on_delete=models.CASCADE, related_name="documents")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    title = models.CharField(max_length=512)
    original_filename = models.CharField(max_length=255, blank=True, null=True)
    mime_type = models.CharField(max_length=255)
    file = models.CharField(max_length=1024, blank=True, null=True)
    sha256 = models.CharField(max_length=64, blank=True, null=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    extracted_text = models.TextField(default="")
    analysis_summary = models.TextField(default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "documents"
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["source", "status"]),
            models.Index(fields=["sha256"]),
        ]

    def __str__(self):
        return self.title


class DocumentAsset(models.Model):
    class AssetType(models.TextChoices):
        IMAGE = "image", "Image"
        PAGE = "page", "Page"
        TABLE = "table", "Table"
        AUDIO = "audio", "Audio"
        VIDEO_FRAME = "video_frame", "Video Frame"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=30, choices=AssetType.choices)
    file = models.CharField(max_length=1024, blank=True, null=True)
    mime_type = models.CharField(max_length=255, blank=True, null=True)
    page_number = models.IntegerField(null=True, blank=True)
    text = models.TextField(default="")
    analysis = models.TextField(default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_assets"
        indexes = [
            models.Index(fields=["document", "asset_type"]),
            models.Index(fields=["document", "page_number"]),
        ]

    def __str__(self):
        return f"{self.asset_type} - {self.document.title}"


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    asset = models.ForeignKey(DocumentAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="chunks")
    chunk_index = models.IntegerField()
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    token_count = models.IntegerField(default=0)
    embedding = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_chunks"
        constraints = [
            models.UniqueConstraint(fields=["document", "chunk_index"], name="unique_document_chunk")
        ]
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
            models.Index(fields=["content_hash"]),
        ]

    def __str__(self):
        return f"Chunk {self.chunk_index} of {self.document.title}"
