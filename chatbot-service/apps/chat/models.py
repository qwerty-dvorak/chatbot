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

    def __str__(self) -> str:
        return self.title


class ChatGrant(models.Model):
    class Role(models.TextChoices):
        VIEWER = "viewer", "Viewer"
        EDITOR = "editor", "Editor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name="grants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_grants"
    )
    role = models.CharField(max_length=30, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_grants"
        constraints = [
            models.UniqueConstraint(
                fields=["chat", "user"], name="unique_chat_grant"
            )
        ]
        indexes = [
            models.Index(fields=["chat"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} ({self.role}) on {self.chat_id}"


class ChatBranch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name="branches"
    )
    name = models.CharField(max_length=255, default="main")
    base_message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branches_from",
    )
    head_message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branches_to",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_branches"
        constraints = [
            models.UniqueConstraint(
                fields=["chat", "name"], name="unique_chat_branch_name"
            )
        ]
        indexes = [
            models.Index(fields=["chat", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.chat_id})"


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
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    role = models.CharField(max_length=30, choices=Role.choices)
    content = models.TextField(default="")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.COMPLETED)
    tool_invocations = models.JSONField(default=dict, blank=True)
    attachments = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    edit_count = models.PositiveIntegerField(default=0)
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messages"
        indexes = [
            models.Index(fields=["chat", "created_at"]),
            models.Index(fields=["chat", "role", "-created_at"]),
            models.Index(fields=["chat", "status", "-created_at"]),
            models.Index(fields=["author"]),
        ]

    def __str__(self) -> str:
        return f"[{self.role}] {self.content[:60]}"


class MessageEdit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="edits"
    )
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_edits",
    )
    previous_content = models.TextField(default="")
    new_content = models.TextField(default="")
    previous_metadata = models.JSONField(default=dict, blank=True)
    new_metadata = models.JSONField(default=dict, blank=True)
    superseded_message_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_edits"
        indexes = [
            models.Index(
                fields=["message", "-created_at"],
                name="message_edi_message_e057a3_idx",
            ),
            models.Index(
                fields=["editor", "-created_at"],
                name="message_edi_editor__07a020_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Edit {self.id} on {self.message_id}"


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

    def __str__(self) -> str:
        return f"Delta {self.sequence} [{self.delta_type}]"


class MessageAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachment_objects"
    )
    document_reference = models.ForeignKey(
        "documents.DocumentReference",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_attachments",
    )
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_attachments"
        indexes = [
            models.Index(fields=["message"]),
            models.Index(fields=["document_reference"]),
        ]

    def __str__(self) -> str:
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

    def __str__(self) -> str:
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

    def __str__(self) -> str:
        return f"{'Up' if self.is_upvoted else 'Down'}vote by {self.user_id}"


class TurnRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name="turn_runs"
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="turn_runs"
    )
    branch = models.ForeignKey(
        ChatBranch, on_delete=models.SET_NULL, null=True, blank=True, related_name="turn_runs"
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.QUEUED
    )
    worker_id = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(default="", blank=True)
    trace = models.ForeignKey(
        "observability.ExecutionTrace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turn_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "turn_runs"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["chat", "-created_at"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["worker_id"]),
        ]

    def __str__(self) -> str:
        return f"TurnRun {self.id} ({self.status})"


class TurnEvent(models.Model):
    class EventType(models.TextChoices):
        PROGRESS = "progress", "Progress"
        TEXT_DELTA = "text_delta", "Text Delta"
        TOOL_CALL = "tool_call", "Tool Call"
        TOOL_RESULT = "tool_result", "Tool Result"
        ERROR = "error", "Error"
        DONE = "done", "Done"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    turn_run = models.ForeignKey(
        TurnRun, on_delete=models.CASCADE, related_name="events"
    )
    sequence = models.IntegerField()
    event_type = models.CharField(
        max_length=30, choices=EventType.choices, default=EventType.PROGRESS
    )
    content = models.TextField(default="", blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "turn_events"
        constraints = [
            models.UniqueConstraint(
                fields=["turn_run", "sequence"], name="unique_turn_event_sequence"
            )
        ]
        indexes = [
            models.Index(fields=["turn_run", "sequence"]),
            models.Index(fields=["turn_run", "event_type"]),
        ]

    def __str__(self) -> str:
        return f"Event {self.sequence} [{self.event_type}]"
