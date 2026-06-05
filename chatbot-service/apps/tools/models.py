import uuid

from django.conf import settings
from django.db import models


class ToolDefinition(models.Model):
    class PermissionLevel(models.TextChoices):
        USER = "user", "User"
        STAFF = "staff", "Staff"
        ADMIN = "admin", "Admin"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    display_name = models.CharField(max_length=255)
    description = models.TextField(default="")
    version = models.CharField(max_length=50, default="1")
    schema = models.JSONField(default=dict, blank=True)
    result_schema = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    is_builtin = models.BooleanField(default=True)
    requires_confirmation = models.BooleanField(default=False)
    permission_level = models.CharField(
        max_length=30, choices=PermissionLevel.choices, default=PermissionLevel.USER
    )
    timeout_seconds = models.IntegerField(default=60)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tool_definitions"
        indexes = [
            models.Index(fields=["is_enabled", "permission_level"]),
        ]

    def __str__(self):
        return self.name


class ToolCall(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        VALIDATED = "validated", "Validated"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        DENIED = "denied", "Denied"
        CANCELLED = "cancelled", "Cancelled"
        TIMED_OUT = "timed_out", "Timed Out"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool_call_id = models.CharField(max_length=255, blank=True, null=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tool_calls"
    )
    chat = models.ForeignKey(
        "chat.Chat", on_delete=models.CASCADE, related_name="tool_calls"
    )
    message = models.ForeignKey(
        "chat.Message", on_delete=models.CASCADE, related_name="tool_calls"
    )
    tool = models.ForeignKey(
        ToolDefinition, on_delete=models.RESTRICT, related_name="calls"
    )
    name_snapshot = models.CharField(max_length=120)
    version_snapshot = models.CharField(max_length=50)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.REQUESTED
    )
    arguments = models.JSONField(default=dict, blank=True)
    raw_arguments = models.JSONField(default=dict, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    permission_result = models.JSONField(default=dict, blank=True)
    sequence = models.IntegerField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tool_calls"
        constraints = [
            models.UniqueConstraint(fields=["message", "sequence"], name="unique_tool_call_sequence")
        ]
        indexes = [
            models.Index(fields=["chat", "sequence"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["tool", "status", "-created_at"]),
            models.Index(fields=["message", "sequence"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.name_snapshot} [{self.status}]"


class ToolExecution(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        TIMED_OUT = "timed_out", "Timed Out"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool_call = models.ForeignKey(
        ToolCall, on_delete=models.CASCADE, related_name="executions"
    )
    attempt = models.IntegerField(default=1)
    worker_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.RUNNING
    )
    input_snapshot = models.JSONField(default=dict, blank=True)
    stdout = models.TextField(default="")
    stderr = models.TextField(default="")
    error_type = models.CharField(max_length=255, blank=True, null=True)
    error_message = models.TextField(default="")
    duration_ms = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tool_executions"
        constraints = [
            models.UniqueConstraint(fields=["tool_call", "attempt"], name="unique_tool_execution_attempt")
        ]
        indexes = [
            models.Index(fields=["tool_call", "attempt"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Attempt {self.attempt} - {self.status}"


class ToolResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool_call = models.ForeignKey(
        ToolCall, on_delete=models.CASCADE, related_name="results"
    )
    execution = models.ForeignKey(
        ToolExecution, on_delete=models.SET_NULL, null=True, blank=True, related_name="results"
    )
    content = models.TextField(default="")
    structured_content = models.JSONField(default=dict, blank=True)
    artifact = models.CharField(max_length=1024, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tool_results"
        indexes = [
            models.Index(fields=["tool_call"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Result for {self.tool_call_id}"


class ToolPermissionGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tool = models.ForeignKey(
        ToolDefinition, on_delete=models.CASCADE, related_name="permission_grants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="tool_permission_grants"
    )
    role = models.CharField(max_length=50, blank=True, null=True)
    is_allowed = models.BooleanField(default=True)
    constraints = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tool_permission_grants"
        indexes = [
            models.Index(fields=["tool", "user"]),
            models.Index(fields=["tool", "role"]),
        ]

    def __str__(self):
        return f"Grant for {self.tool.name}"
