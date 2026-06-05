from django.contrib import admin

from .models import Chat, ChatShare, Message, MessageAttachment, MessageDelta, Vote


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "status", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "archived", "created_at", "updated_at")
    list_filter = ("archived",)
    search_fields = ("title", "user__email")
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "chat", "role", "status", "created_at")
    list_filter = ("role", "status")
    search_fields = ("content",)


@admin.register(MessageDelta)
class MessageDeltaAdmin(admin.ModelAdmin):
    list_display = ("message", "sequence", "delta_type", "created_at")
    list_filter = ("delta_type",)


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "mime_type", "size_bytes", "analysis_status", "message")
    list_filter = ("analysis_status", "mime_type")


@admin.register(ChatShare)
class ChatShareAdmin(admin.ModelAdmin):
    list_display = ("token", "chat", "user", "revoked", "expires_at", "created_at")
    list_filter = ("revoked",)


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("message", "user", "is_upvoted", "created_at")
