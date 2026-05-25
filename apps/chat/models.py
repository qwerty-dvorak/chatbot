import uuid

from django.conf import settings
from django.db import models


class Chat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chats"
    )
    title = models.CharField(max_length=255)
    path = models.CharField(max_length=512)
    archived = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chats"
        constraints = [
            models.UniqueConstraint(fields=["user", "path"], name="unique_user_chat_path")
        ]
        indexes = [
            models.Index(fields=["user", "archived", "-updated_at"]),
            models.Index(fields=["user", "-updated_at"]),
            models.Index(fields=["archived"]),
        ]

    def __str__(self):
        return self.title


class Message(models.Model):
    class Role(models.TextChoices):
        SYSTEM = "system", "System"
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        STREAMING = "streaming", "Streaming"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name="messages"
    )
    parent_message = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="child_messages"
    )
    role = models.CharField(max_length=30, choices=Role.choices)
    content = models.TextField(default="")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.COMPLETED)
    tool_invocations = models.JSONField(default=dict, blank=True)
    attachments = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messages"
        indexes = [
            models.Index(fields=["chat", "created_at"]),
            models.Index(fields=["chat", "role", "-created_at"]),
            models.Index(fields=["chat", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.role}] {self.content[:60]}"


class MessageDelta(models.Model):
    class DeltaType(models.TextChoices):
        TEXT = "text", "Text"
        TOOL_CALL = "tool_call", "Tool Call"
        TOOL_RESULT = "tool_result", "Tool Result"
        ERROR = "error", "Error"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="deltas"
    )
    sequence = models.IntegerField()
    delta_type = models.CharField(max_length=30, choices=DeltaType.choices, default=DeltaType.TEXT)
    content = models.TextField(default="")
    raw_event = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_deltas"
        constraints = [
            models.UniqueConstraint(fields=["message", "sequence"], name="unique_message_delta_sequence")
        ]
        indexes = [
            models.Index(fields=["message", "sequence"]),
        ]

    def __str__(self):
        return f"Delta {self.sequence} [{self.delta_type}]"


class MessageAttachment(models.Model):
    class AnalysisStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachment_objects"
    )
    file = models.CharField(max_length=1024)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    analysis_status = models.CharField(
        max_length=30, choices=AnalysisStatus.choices, default=AnalysisStatus.PENDING
    )
    analysis_text = models.TextField(default="")
    analysis_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_attachments"
        indexes = [
            models.Index(fields=["message"]),
            models.Index(fields=["sha256"]),
        ]

    def __str__(self):
        return self.original_filename


class ChatShare(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name="shares"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_shares"
    )
    token = models.CharField(max_length=128, unique=True)
    title = models.CharField(max_length=255, blank=True, null=True)
    revoked = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_shares"
        indexes = [
            models.Index(fields=["chat", "revoked"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["token"]),
        ]

    def __str__(self):
        return f"Share {self.token[:8]}..."


class Vote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name="votes"
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="votes"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="votes"
    )
    is_upvoted = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "votes"
        constraints = [
            models.UniqueConstraint(fields=["user", "message"], name="unique_user_message_vote")
        ]
        indexes = [
            models.Index(fields=["chat"]),
            models.Index(fields=["message"]),
        ]

    def __str__(self):
        return f"{'Up' if self.is_upvoted else 'Down'}vote by {self.user_id}"
