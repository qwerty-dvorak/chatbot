from django.contrib import admin

from apps.documents.models import ArtifactRevision, ContentBlob, DocumentGrant, DocumentReference, EmbeddingSet


@admin.register(DocumentReference)
class DocumentReferenceAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "kind", "created_at")
    list_filter = ("kind",)
    search_fields = ("title", "owner__email")


@admin.register(DocumentGrant)
class DocumentGrantAdmin(admin.ModelAdmin):
    list_display = ("document_reference", "user", "role", "created_at")
    list_filter = ("role",)


@admin.register(ContentBlob)
class ContentBlobAdmin(admin.ModelAdmin):
    list_display = ("content_hash", "mime_type", "size_bytes", "storage_status", "created_at")
    list_filter = ("storage_status", "mime_type")


@admin.register(ArtifactRevision)
class ArtifactRevisionAdmin(admin.ModelAdmin):
    list_display = ("blob", "pipeline_fingerprint", "processing_status", "created_at")
    list_filter = ("processing_status",)


@admin.register(EmbeddingSet)
class EmbeddingSetAdmin(admin.ModelAdmin):
    list_display = ("artifact_revision", "model_name", "embedding_type", "dimensions")
    list_filter = ("embedding_type", "model_name")