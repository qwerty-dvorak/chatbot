import uuid

from django.test import TestCase

from apps.accounts.models import User
from apps.chat.models import Chat, Message
from apps.tools.builtin import BUILTIN_TOOLS
from apps.tools.executor import ToolExecutor
from apps.tools.models import (
    ToolCall,
    ToolDefinition,
    ToolExecution,
    ToolPermissionGrant,
    ToolResult,
)
from apps.tools.registry import registry


class ToolDefinitionModelTest(TestCase):
    def test_create_tool_definition(self):
        tool = ToolDefinition.objects.create(
            name="test.tool",
            display_name="Test Tool",
            description="A test tool",
            schema={"type": "object", "properties": {}},
        )
        self.assertEqual(tool.name, "test.tool")
        self.assertTrue(tool.is_enabled)
        self.assertTrue(tool.is_builtin)
        self.assertEqual(tool.permission_level, "user")

    def test_tool_name_unique(self):
        ToolDefinition.objects.create(name="unique.tool", display_name="Tool 1")
        with self.assertRaises(Exception):
            ToolDefinition.objects.create(name="unique.tool", display_name="Tool 2")

    def test_tool_str(self):
        tool = ToolDefinition.objects.create(name="test.tool", display_name="Test")
        self.assertEqual(str(tool), "test.tool")


class ToolCallModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(name="test.tool", display_name="Test")

    def test_create_tool_call(self):
        call = ToolCall.objects.create(
            tool_call_id="call_123",
            user=self.user,
            chat=self.chat,
            message=self.msg,
            tool=self.tool_def,
            name_snapshot="test.tool",
            version_snapshot="1",
            arguments={"key": "value"},
            raw_arguments={"key": "value"},
            sequence=0,
        )
        self.assertEqual(call.status, "requested")
        self.assertEqual(call.arguments["key"], "value")

    def test_tool_call_unique_sequence(self):
        ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t1", version_snapshot="1",
            sequence=0,
        )
        with self.assertRaises(Exception):
            ToolCall.objects.create(
                user=self.user, chat=self.chat, message=self.msg,
                tool=self.tool_def, name_snapshot="t2", version_snapshot="1",
                sequence=0,
            )

    def test_tool_call_status_choices(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0, status=ToolCall.Status.SUCCEEDED,
        )
        self.assertEqual(call.status, "succeeded")

    def test_tool_call_str(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="test.tool", version_snapshot="1",
            sequence=0,
        )
        self.assertIn("test.tool", str(call))


class ToolExecutionModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(name="test.tool", display_name="Test")
        self.call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="test.tool", version_snapshot="1",
            sequence=0,
        )

    def test_create_execution(self):
        exec = ToolExecution.objects.create(tool_call=self.call, attempt=1)
        self.assertEqual(exec.status, "running")
        self.assertIsNotNone(exec.started_at)

    def test_execution_unique_attempt(self):
        ToolExecution.objects.create(tool_call=self.call, attempt=1)
        with self.assertRaises(Exception):
            ToolExecution.objects.create(tool_call=self.call, attempt=1)

    def test_execution_str(self):
        exec = ToolExecution.objects.create(tool_call=self.call, attempt=1)
        self.assertIn("1", str(exec))


class ToolResultModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(name="test.tool", display_name="Test")
        self.call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="test.tool", version_snapshot="1",
            sequence=0,
        )

    def test_create_result(self):
        result = ToolResult.objects.create(tool_call=self.call, content="Result text")
        self.assertEqual(result.content, "Result text")
        self.assertEqual(result.structured_content, {})

    def test_result_str(self):
        result = ToolResult.objects.create(tool_call=self.call, content="Done")
        self.assertIn(str(self.call.id), str(result))


class ToolPermissionGrantModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.tool_def = ToolDefinition.objects.create(name="test.tool", display_name="Test")

    def test_create_grant(self):
        grant = ToolPermissionGrant.objects.create(
            tool=self.tool_def, user=self.user, is_allowed=False
        )
        self.assertFalse(grant.is_allowed)

    def test_grant_str(self):
        grant = ToolPermissionGrant.objects.create(
            tool=self.tool_def, user=self.user
        )
        self.assertIn("test.tool", str(grant))


class ToolRegistryTest(TestCase):
    def setUp(self):
        self.tool = ToolDefinition.objects.create(
            name="test.registry",
            display_name="Registry Test",
            schema={"type": "object", "properties": {}},
        )

    def test_register_and_get(self):
        registry.register(self.tool)
        self.assertEqual(registry.get("test.registry"), self.tool)

    def test_get_enabled(self):
        disabled = ToolDefinition.objects.create(
            name="disabled.tool", display_name="Disabled", is_enabled=False
        )
        registry.register(self.tool)
        registry.register(disabled)
        enabled = registry.get_enabled()
        self.assertIn(self.tool, enabled)
        self.assertNotIn(disabled, enabled)

    def test_unregister(self):
        registry.register(self.tool)
        registry.unregister("test.registry")
        self.assertIsNone(registry.get("test.registry"))

    def test_refresh_from_db(self):
        registry.register(self.tool)
        new_tool = ToolDefinition.objects.create(
            name="refreshed.tool", display_name="Refreshed"
        )
        registry.refresh_from_db()
        self.assertIsNotNone(registry.get("refreshed.tool"))


class BuiltinToolsTest(TestCase):
    def test_all_builtins_are_callable(self):
        for name, handler in BUILTIN_TOOLS.items():
            result = handler({})
            self.assertIsInstance(result, dict)
            self.assertIn("message", result)

    def test_rag_search_default(self):
        result = BUILTIN_TOOLS["rag.search"]({"query": "test"})
        self.assertEqual(result["results"], [])

    def test_memory_save_default(self):
        result = BUILTIN_TOOLS["memory.save"]({"content": "test memory"})
        self.assertTrue(result["saved"])


class ToolExecutorTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(
            name="memory.save",
            display_name="Memory Save",
            schema={"type": "object", "properties": {"content": {"type": "string"}}},
        )
        registry.refresh_from_db()
        self.executor = ToolExecutor(registry)

    def test_execute_creates_execution_and_result(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="memory.save", version_snapshot="1",
            arguments={"content": "test"}, raw_arguments={"content": "test"},
            sequence=0,
        )
        result = self.executor.execute(call)
        self.assertIsNotNone(result)
        self.assertEqual(ToolExecution.objects.filter(tool_call=call).count(), 1)
        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(execution.status, "succeeded")

    def test_execute_nonexistent_tool(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="nonexistent.tool", version_snapshot="1",
            arguments={}, raw_arguments={}, sequence=0,
        )
        result = self.executor.execute(call)
        self.assertIn("Error", result.content)
        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(execution.status, "failed")


class ToolSyncCommandTest(TestCase):
    def test_sync_creates_builtins(self):
        from django.core.management import call_command
        call_command("sync_builtin_tools")
        self.assertTrue(ToolDefinition.objects.filter(name="rag.search").exists())
        self.assertTrue(ToolDefinition.objects.filter(name="memory.save").exists())
        self.assertTrue(ToolDefinition.objects.filter(name="chat.compact").exists())
        self.assertEqual(ToolDefinition.objects.filter(is_builtin=True).count(), 6)
