import uuid

from django.db import models


class KnowledgeSource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    source_type = models.CharField(max_length=50)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "knowledge_sources"
        indexes = [
            models.Index(fields=["source_type", "name"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.source_type})"


class KnowledgeDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        KnowledgeSource, on_delete=models.CASCADE, related_name="documents"
    )
    content_blob = models.ForeignKey(
        "documents.ContentBlob", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="knowledge_documents"
    )
    title = models.CharField(max_length=512)
    original_filename = models.CharField(max_length=1024, blank=True, default="")
    mime_type = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=30, default="pending")
    extracted_text = models.TextField(default="", blank=True)
    analysis_summary = models.TextField(default="", blank=True)
    ocr_mode = models.CharField(max_length=50, blank=True, default="none")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "documents"

    def __str__(self) -> str:
        return self.title


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    asset = models.ForeignKey(
        "DocumentAsset", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="chunks"
    )
    chunk_index = models.IntegerField(default=0)
    content = models.TextField()
    content_hash = models.CharField(max_length=64, blank=True, default="")
    token_count = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_chunks"
        indexes = [
            models.Index(fields=["document", "chunk_index"]),
        ]

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} of {self.document_id}"


class DocumentAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="assets"
    )
    asset_type = models.CharField(max_length=50)
    mime_type = models.CharField(max_length=255, blank=True, default="")
    page_number = models.IntegerField(null=True, blank=True)
    text = models.TextField(default="", blank=True)
    analysis = models.TextField(default="", blank=True)
    source_index = models.IntegerField(default=0)
    derived_index = models.IntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)
    ocr_backend = models.CharField(max_length=50, default="none")
    ocr_status = models.CharField(max_length=30, default="skipped")
    preprocessing = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_assets"
        indexes = [
            models.Index(fields=["document", "asset_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.asset_type} ({self.document_id})"
