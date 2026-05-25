from django.contrib import admin

from .models import ToolCall, ToolDefinition, ToolExecution, ToolPermissionGrant, ToolResult


@admin.register(ToolDefinition)
class ToolDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "display_name", "is_enabled", "is_builtin", "permission_level", "version")
    list_filter = ("is_enabled", "is_builtin", "permission_level")
    search_fields = ("name", "display_name", "description")
    actions = ["enable_tools", "disable_tools"]

    def enable_tools(self, request, queryset):
        queryset.update(is_enabled=True)
    enable_tools.short_description = "Enable selected tools"

    def disable_tools(self, request, queryset):
        queryset.update(is_enabled=False)
    disable_tools.short_description = "Disable selected tools"


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("name_snapshot", "tool", "user", "chat", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name_snapshot", "user__email", "chat__title")


@admin.register(ToolExecution)
class ToolExecutionAdmin(admin.ModelAdmin):
    list_display = ("tool_call", "attempt", "status", "duration_ms", "created_at")
    list_filter = ("status",)


@admin.register(ToolResult)
class ToolResultAdmin(admin.ModelAdmin):
    list_display = ("tool_call", "execution", "created_at")


@admin.register(ToolPermissionGrant)
class ToolPermissionGrantAdmin(admin.ModelAdmin):
    list_display = ("tool", "user", "role", "is_allowed", "created_at")
    list_filter = ("is_allowed",)
