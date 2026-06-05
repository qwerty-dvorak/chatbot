from django.contrib import admin

from .models import Document, DocumentAsset, DocumentChunk, KnowledgeSource
from .retrieval_models import RetrievalHit, RetrievalRun


@admin.register(KnowledgeSource)
class KnowledgeSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "source_type", "visibility", "created_at")
    list_filter = ("source_type", "visibility")
    search_fields = ("name", "owner__email")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "owner", "mime_type", "status", "created_at")
    list_filter = ("status", "mime_type")
    search_fields = ("title", "original_filename")
    actions = ["mark_ready", "re_embed"]

    def mark_ready(self, request, queryset):
        queryset.update(status="ready")
    mark_ready.short_description = "Mark selected as ready"

    def re_embed(self, request, queryset):
        for doc in queryset:
            doc.status = "pending"
            doc.save(update_fields=["status"])
    re_embed.short_description = "Re-embed selected documents"


@admin.register(DocumentAsset)
class DocumentAssetAdmin(admin.ModelAdmin):
    list_display = ("document", "asset_type", "page_number", "mime_type")
    list_filter = ("asset_type",)


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "chunk_index", "token_count", "created_at")
    list_filter = ("document",)


@admin.register(RetrievalRun)
class RetrievalRunAdmin(admin.ModelAdmin):
    list_display = ("query", "user", "strategy", "created_at")
    list_filter = ("strategy",)


@admin.register(RetrievalHit)
class RetrievalHitAdmin(admin.ModelAdmin):
    list_display = ("run", "chunk", "rank", "score", "source_title")
