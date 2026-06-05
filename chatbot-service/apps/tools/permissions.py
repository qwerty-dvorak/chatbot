from .models import ToolDefinition, ToolPermissionGrant


def check_tool_permission(tool: ToolDefinition, user) -> bool:
    if not tool.is_enabled:
        return False

    if tool.permission_level == ToolDefinition.PermissionLevel.SYSTEM:
        return user is not None and user.is_superuser
    if tool.permission_level == ToolDefinition.PermissionLevel.ADMIN:
        return user is not None and user.is_staff
    if tool.permission_level == ToolDefinition.PermissionLevel.STAFF:
        return user is not None and user.is_staff
    if tool.permission_level == ToolDefinition.PermissionLevel.USER:
        return user is not None and user.is_authenticated

    return False


def check_user_tool_override(tool: ToolDefinition, user) -> ToolPermissionGrant | None:
    if not user or not user.is_authenticated:
        return None
    return ToolPermissionGrant.objects.filter(
        tool=tool, user=user
    ).first()
