from django.test import TestCase

from apps.accounts.models import User
from apps.tools.models import ToolDefinition, ToolPermissionGrant
from apps.tools.permissions import check_tool_permission, check_user_tool_override


class ToolPermissionsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@example.com", password="pass123")
        self.staff = User.objects.create_user(
            email="staff@example.com", password="pass123", is_staff=True
        )
        self.admin = User.objects.create_user(
            email="admin@example.com", password="pass123", is_staff=True, is_superuser=True
        )
        self.user_tool = ToolDefinition.objects.create(
            name="user.tool", display_name="User Tool", permission_level="user"
        )
        self.staff_tool = ToolDefinition.objects.create(
            name="staff.tool", display_name="Staff Tool", permission_level="staff"
        )
        self.admin_tool = ToolDefinition.objects.create(
            name="admin.tool", display_name="Admin Tool", permission_level="admin"
        )
        self.system_tool = ToolDefinition.objects.create(
            name="system.tool", display_name="System Tool", permission_level="system"
        )

    def test_user_can_access_user_tool(self):
        self.assertTrue(check_tool_permission(self.user_tool, self.user))

    def test_user_cannot_access_staff_tool(self):
        self.assertFalse(check_tool_permission(self.staff_tool, self.user))

    def test_staff_can_access_staff_tool(self):
        self.assertTrue(check_tool_permission(self.staff_tool, self.staff))

    def test_staff_cannot_access_system_tool(self):
        self.assertFalse(check_tool_permission(self.system_tool, self.staff))

    def test_admin_can_access_system_tool(self):
        self.assertTrue(check_tool_permission(self.system_tool, self.admin))

    def test_disabled_tool_returns_false(self):
        self.user_tool.is_enabled = False
        self.user_tool.save()
        self.assertFalse(check_tool_permission(self.user_tool, self.user))

    def test_unauthenticated_user_cannot_access_any_tool(self):
        self.assertFalse(check_tool_permission(self.user_tool, None))

    def test_check_user_tool_override_returns_none_if_no_grant(self):
        self.assertIsNone(check_user_tool_override(self.user_tool, self.user))

    def test_check_user_tool_override_returns_grant(self):
        grant = ToolPermissionGrant.objects.create(
            tool=self.user_tool, user=self.user, is_allowed=False
        )
        result = check_user_tool_override(self.user_tool, self.user)
        self.assertEqual(result, grant)
