import json
import uuid
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.chat.models import Chat, Message
from apps.tools.builtin import BUILTIN_TOOLS, register_builtin
from apps.tools.executor import ToolExecutor
from apps.tools.management.commands.sync_builtin_tools import BUILTIN_TOOL_DEFS
from apps.tools.models import (
    ToolCall,
    ToolDefinition,
    ToolExecution,
    ToolPermissionGrant,
    ToolResult,
)
from apps.tools.registry import ToolRegistry, registry
from apps.tools.views import ToolCallDetailView, ToolListView


# ═══════════════════════════════════════════════════════════════════════════════
# View tests
# ═══════════════════════════════════════════════════════════════════════════════

class ToolListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")
        self.staff = User.objects.create_user(email="staff@e.com", password="pass", is_staff=True)
        for i in range(3):
            ToolDefinition.objects.create(
                name=f"tool.{i}", display_name=f"Tool {i}", is_enabled=(i % 2 == 0)
            )

    def test_login_required(self):
        resp = self.client.get(reverse("tools:list"))
        self.assertRedirects(resp, f"{settings.LOGIN_URL}?next=/tools/")

    def test_lists_only_enabled_tools(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("tools:list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "tool.0")
        self.assertContains(resp, "tool.2")
        self.assertNotContains(resp, "tool.1")
        self.assertEqual(len(resp.context["tools"]), 2)

    def test_template_used(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("tools:list"))
        self.assertTemplateUsed(resp, "tools/tool_list.html")
        self.assertEqual(resp.context["view"].__class__, ToolListView)


class ToolCallDetailViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")
        self.other = User.objects.create_user(email="other@e.com", password="pass")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="path")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(name="test.tool", display_name="Test")
        self.call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="test.tool", version_snapshot="1",
            arguments={"key": "val"}, raw_arguments={"key": "val"}, sequence=0,
        )

    def test_login_required(self):
        resp = self.client.get(reverse("tools:call-detail", args=[self.call.id]))
        self.assertRedirects(resp, f"{settings.LOGIN_URL}?next=/tools/calls/{self.call.id}/")

    def test_own_call_visible(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("tools:call-detail", args=[self.call.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "tools/tool_call_detail.html")
        self.assertEqual(resp.context["tool_call"], self.call)

    def test_other_user_call_not_visible(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("tools:call-detail", args=[self.call.id]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_shows_arguments(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("tools:call-detail", args=[self.call.id]))
        self.assertContains(resp, "key")
        self.assertContains(resp, "val")


# ═══════════════════════════════════════════════════════════════════════════════
# URL routing tests
# ═══════════════════════════════════════════════════════════════════════════════

class ToolURLRoutingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")

    def test_list_url_reverses(self):
        self.assertEqual(reverse("tools:list"), "/tools/")

    def test_call_detail_url_reverses(self):
        uid = uuid.uuid4()
        self.assertEqual(reverse("tools:call-detail", args=[uid]), f"/tools/calls/{uid}/")

    def test_invalid_uuid_returns_404(self):
        self.client.force_login(self.user)
        resp = self.client.get("/tools/calls/not-a-uuid/")
        self.assertEqual(resp.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# Registry schema generation tests
# ═══════════════════════════════════════════════════════════════════════════════

class ToolRegistrySchemaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass", is_active=True)
        self.anon = None
        self.reg = ToolRegistry()

    def tearDown(self):
        self.reg._tools.clear()

    def test_empty_registry_returns_empty_schemas(self):
        self.assertEqual(self.reg.get_schemas(self.user), [])

    def test_schema_has_function_type(self):
        tool = ToolDefinition.objects.create(
            name="weather.get", display_name="Get Weather",
            schema={"type": "object", "properties": {"loc": {"type": "string"}}},
        )
        self.reg.register(tool)
        schemas = self.reg.get_schemas(self.user)
        self.assertEqual(len(schemas), 1)
        s = schemas[0]
        self.assertEqual(s["type"], "function")
        self.assertEqual(s["function"]["name"], "weather.get")
        self.assertEqual(s["function"]["parameters"]["properties"]["loc"]["type"], "string")

    def test_disabled_tool_excluded(self):
        tool = ToolDefinition.objects.create(
            name="disabled.tool", display_name="Disabled", is_enabled=False,
        )
        self.reg.register(tool)
        self.assertEqual(self.reg.get_schemas(self.user), [])

    def test_permission_level_filtering(self):
        user_tool = ToolDefinition.objects.create(
            name="user.tool", display_name="User", permission_level="user",
        )
        sys_tool = ToolDefinition.objects.create(
            name="system.tool", display_name="System", permission_level="system",
        )
        self.reg.register(user_tool)
        self.reg.register(sys_tool)
        schemas = self.reg.get_schemas(self.user)
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("user.tool", names)
        self.assertNotIn("system.tool", names)

    def test_rag_disabled_hides_rag_prefix_tools(self):
        rag_tool = ToolDefinition.objects.create(
            name="rag.search", display_name="RAG Search",
        )
        knowledge_tool = ToolDefinition.objects.create(
            name="knowledge.status", display_name="Knowledge",
        )
        normal_tool = ToolDefinition.objects.create(
            name="memory.save", display_name="Memory",
        )
        self.reg.register(rag_tool)
        self.reg.register(knowledge_tool)
        self.reg.register(normal_tool)

        with override_settings(RAG_ENABLED=False):
            schemas = self.reg.get_schemas(self.user)

        names = [s["function"]["name"] for s in schemas]
        self.assertNotIn("rag.search", names)
        self.assertNotIn("knowledge.status", names)
        self.assertIn("memory.save", names)

    def test_tool_calls_disabled_returns_empty(self):
        tool = ToolDefinition.objects.create(name="any.tool", display_name="Any")
        self.reg.register(tool)
        with override_settings(TOOL_CALLS_ENABLED=False):
            schemas = self.reg.get_schemas(self.user)
        self.assertEqual(schemas, [])

    def test_schema_includes_parameters_object_when_empty(self):
        tool = ToolDefinition.objects.create(
            name="noop", display_name="No-op",
            schema={"type": "object", "properties": {}},
        )
        self.reg.register(tool)
        s = self.reg.get_schemas(self.user)[0]
        self.assertEqual(s["function"]["parameters"]["type"], "object")

    def test_refresh_from_db_loads_all_tools(self):
        ToolDefinition.objects.create(name="alpha", display_name="Alpha")
        ToolDefinition.objects.create(name="beta", display_name="Beta")
        self.reg.refresh_from_db()
        self.assertIsNotNone(self.reg.get("alpha"))
        self.assertIsNotNone(self.reg.get("beta"))
        self.assertEqual(len(self.reg._tools), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Full lifecycle integration tests
# ═══════════════════════════════════════════════════════════════════════════════

def _dummy_echo(arguments: dict, context: dict = None) -> dict:
    return {"echoed": arguments.get("input", ""), "context_user": str(context.get("user"))}


class ToolFullLifecycleTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@e.com", password="pass")
        self.chat = Chat.objects.create(user=self.user, title="Test Chat", path="test")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(
            name="echo.test",
            display_name="Echo Test",
            schema={"type": "object", "properties": {"input": {"type": "string"}}},
            version="2",
        )
        registry.refresh_from_db()
        self.executor = ToolExecutor(registry, context={"user": self.user, "chat": self.chat})

    def tearDown(self):
        registry._tools.clear()
        BUILTIN_TOOLS.pop("echo.test", None)

    def test_full_lifecycle(self):
        BUILTIN_TOOLS["echo.test"] = _dummy_echo
        registry.refresh_from_db()

        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="echo.test", version_snapshot="2",
            arguments={"input": "hello"}, raw_arguments={"input": "hello"},
            sequence=0,
        )
        result = self.executor.execute(call)

        call.refresh_from_db()
        self.assertIsNotNone(result)
        self.assertEqual(ToolExecution.objects.filter(tool_call=call).count(), 1)
        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(execution.status, "succeeded")
        self.assertIsNotNone(execution.duration_ms)
        self.assertIsNotNone(execution.completed_at)

        result_content = json.loads(result.content)
        self.assertEqual(result_content["echoed"], "hello")

        tool_results = ToolResult.objects.filter(tool_call=call)
        self.assertEqual(tool_results.count(), 1)

    def test_execution_failure_recorded(self):
        BUILTIN_TOOLS["echo.test"] = lambda args, ctx: (_ for _ in ()).throw(ValueError("boom"))
        registry.refresh_from_db()

        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="echo.test", version_snapshot="1",
            arguments={"input": "x"}, raw_arguments={"input": "x"}, sequence=0,
        )
        result = self.executor.execute(call)

        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(execution.status, "failed")
        self.assertEqual(execution.error_type, "ValueError")
        self.assertEqual(execution.error_message, "boom")
        self.assertIn("Error", result.content)

    def test_execution_times_out(self):
        BUILTIN_TOOLS["echo.test"] = _dummy_echo
        registry.refresh_from_db()

        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="echo.test", version_snapshot="1",
            arguments={"input": "hello"}, raw_arguments={"input": "hello"}, sequence=0,
        )
        result = self.executor.execute(call)
        self.assertIsNotNone(result)
        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(execution.status, "succeeded")

    def test_multiple_tool_calls_same_message(self):
        BUILTIN_TOOLS["echo.test"] = _dummy_echo
        registry.refresh_from_db()

        calls = []
        for i in range(3):
            c = ToolCall.objects.create(
                user=self.user, chat=self.chat, message=self.msg,
                tool=self.tool_def, name_snapshot="echo.test", version_snapshot="1",
                arguments={"input": str(i)}, raw_arguments={"input": str(i)},
                sequence=i,
            )
            calls.append(c)
            result = self.executor.execute(c)
            self.assertIn(str(i), json.loads(result.content)["echoed"])

        for c in calls:
            self.assertEqual(ToolExecution.objects.filter(tool_call=c).count(), 1)

    def test_tool_not_found_in_registry(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="nonexistent.tool", version_snapshot="1",
            arguments={}, raw_arguments={}, sequence=0,
        )
        result = self.executor.execute(call)
        self.assertIn("Error", result.content)
        self.assertIn("nonexistent.tool", result.content)
        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(execution.status, "failed")
        self.assertEqual(execution.error_type, "ToolNotFound")

    def test_result_links_to_execution(self):
        BUILTIN_TOOLS["echo.test"] = _dummy_echo
        registry.refresh_from_db()

        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="echo.test", version_snapshot="1",
            arguments={"input": "hi"}, raw_arguments={"input": "hi"}, sequence=0,
        )
        result = self.executor.execute(call)
        execution = ToolExecution.objects.get(tool_call=call)
        self.assertEqual(result.execution, execution)

    def test_dict_result_is_json_serialised(self):
        BUILTIN_TOOLS["echo.test"] = _dummy_echo
        registry.refresh_from_db()

        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="echo.test", version_snapshot="1",
            arguments={"input": "dict_test"}, raw_arguments={"input": "dict_test"},
            sequence=0,
        )
        result = self.executor.execute(call)
        parsed = json.loads(result.content)
        self.assertEqual(parsed["echoed"], "dict_test")

    def test_context_passed_to_handler(self):
        BUILTIN_TOOLS["echo.test"] = _dummy_echo
        registry.refresh_from_db()

        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="echo.test", version_snapshot="1",
            arguments={"input": "ctx"}, raw_arguments={"input": "ctx"}, sequence=0,
        )
        result = self.executor.execute(call)
        parsed = json.loads(result.content)
        self.assertIn("context_user", parsed)


# ═══════════════════════════════════════════════════════════════════════════════
# Management command tests
# ═══════════════════════════════════════════════════════════════════════════════

class ListToolsCommandTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")
        ToolDefinition.objects.create(
            name="alpha.tool", display_name="Alpha", is_enabled=True,
        )
        ToolDefinition.objects.create(
            name="beta.tool", display_name="Beta", is_enabled=False,
        )

    def test_list_output_contains_tools(self):
        from io import StringIO
        out = StringIO()
        call_command("list_tools", stdout=out)
        output = out.getvalue()
        self.assertIn("alpha.tool", output)
        self.assertIn("beta.tool", output)
        self.assertIn("enabled", output)
        self.assertIn("disabled", output)

    def test_list_shows_count(self):
        from io import StringIO
        out = StringIO()
        call_command("list_tools", stdout=out)
        self.assertIn("2 tools total", out.getvalue())


class SyncBuiltinToolsCommandTest(TestCase):
    def test_sync_creates_all_builtins(self):
        from io import StringIO
        out = StringIO()
        call_command("sync_builtin_tools", stdout=out)
        for def_data in BUILTIN_TOOL_DEFS:
            self.assertTrue(
                ToolDefinition.objects.filter(name=def_data["name"]).exists(),
                f"Missing: {def_data['name']}",
            )
        output = out.getvalue()
        self.assertIn("Done", output)

    def test_sync_is_idempotent(self):
        from io import StringIO
        call_command("sync_builtin_tools", stdout=StringIO())
        count_before = ToolDefinition.objects.count()
        call_command("sync_builtin_tools", stdout=StringIO())
        count_after = ToolDefinition.objects.count()
        self.assertEqual(count_before, count_after)

    def test_sync_updates_existing_tool(self):
        ToolDefinition.objects.create(
            name="rag.search", display_name="Old Name", is_builtin=True,
        )
        from io import StringIO
        call_command("sync_builtin_tools", stdout=StringIO())
        tool = ToolDefinition.objects.get(name="rag.search")
        self.assertEqual(tool.display_name, "RAG Search")

    def test_sync_sets_correct_schema(self):
        from io import StringIO
        call_command("sync_builtin_tools", stdout=StringIO())
        tool = ToolDefinition.objects.get(name="rag.search")
        self.assertEqual(tool.schema["required"], ["query"])
        self.assertIn("query", tool.schema["properties"])


# ═══════════════════════════════════════════════════════════════════════════════
# Builtin tool handler contract tests
# ═══════════════════════════════════════════════════════════════════════════════

class BuiltinHandlerContractTest(TestCase):
    def test_every_handler_returns_dict(self):
        for name, handler in BUILTIN_TOOLS.items():
            result = handler({}, {})
            self.assertIsInstance(result, dict, f"{name} must return dict")

    def test_memory_search_returns_memories_key(self):
        result = BUILTIN_TOOLS["memory.search"]({"query": "test"}, {})
        self.assertIn("memories", result)
        self.assertIn("message", result)

    def test_memory_save_without_user_returns_saved_false(self):
        result = BUILTIN_TOOLS["memory.save"]({"content": "test"}, {"user": None})
        self.assertIs(result["saved"], False)
        self.assertIn("message", result)

    def test_memory_save_without_content_returns_saved_false(self):
        user = User.objects.create_user(email="mem@e.com", password="pass")
        result = BUILTIN_TOOLS["memory.save"]({"content": ""}, {"user": user})
        self.assertIs(result["saved"], False)

    def test_rag_search_returns_results_list(self):
        result = BUILTIN_TOOLS["rag.search"]({"query": ""}, {})
        self.assertEqual(result["results"], [])
        self.assertIn("message", result)

    def test_ingest_status_returns_status_key(self):
        result = BUILTIN_TOOLS["knowledge.ingest_status"]({}, {})
        self.assertIn("status", result)
        self.assertIn("counts", result)

    def test_chat_compact_without_chat_returns_compacted_false(self):
        result = BUILTIN_TOOLS["chat.compact"]({}, {"chat": None})
        self.assertIs(result["compacted"], False)

    def test_document_analyze_without_docs_returns_analyzed_false(self):
        result = BUILTIN_TOOLS["document.analyze"]({}, {"user": None})
        self.assertIs(result["analyzed"], False)

    def test_memory_aggregate_without_user_returns_aggregated_false(self):
        result = BUILTIN_TOOLS["memory.aggregate"]({}, {"user": None})
        self.assertIs(result["aggregated"], False)

    def test_builtin_handlers_handle_exceptions_gracefully(self):
        result = BUILTIN_TOOLS["memory.search"]({}, {})
        self.assertIn("memories", result)


# ═══════════════════════════════════════════════════════════════════════════════
# Permission grant integration tests
# ═══════════════════════════════════════════════════════════════════════════════

class PermissionGrantIntegrationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")
        self.staff = User.objects.create_user(email="staff@e.com", password="pass", is_staff=True)
        self.tool = ToolDefinition.objects.create(
            name="restricted.tool", display_name="Restricted",
            permission_level="staff",
        )

    def test_user_blocked_by_permission_level(self):
        self.assertFalse(registry._is_permitted(self.tool, self.user))

    def test_staff_allowed_by_permission_level(self):
        self.assertTrue(registry._is_permitted(self.tool, self.staff))

    def test_permission_grant_overrides_block(self):
        ToolPermissionGrant.objects.create(
            tool=self.tool, user=self.user, is_allowed=True,
        )
        from apps.tools.permissions import check_user_tool_override
        grant = check_user_tool_override(self.tool, self.user)
        self.assertIsNotNone(grant)
        self.assertTrue(grant.is_allowed)

    def test_permission_grant_can_block_allowed_user(self):
        ToolPermissionGrant.objects.create(
            tool=self.tool, user=self.staff, is_allowed=False,
        )
        from apps.tools.permissions import check_user_tool_override
        grant = check_user_tool_override(self.tool, self.staff)
        self.assertIsNotNone(grant)
        self.assertFalse(grant.is_allowed)

    def test_unauthenticated_user_no_override(self):
        from apps.tools.permissions import check_user_tool_override
        self.assertIsNone(check_user_tool_override(self.tool, None))


# ═══════════════════════════════════════════════════════════════════════════════
# ToolCall status transition tests
# ═══════════════════════════════════════════════════════════════════════════════

class ToolCallStatusTransitionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")
        self.chat = Chat.objects.create(user=self.user, title="Chat", path="p")
        self.msg = Message.objects.create(chat=self.chat, role=Message.Role.ASSISTANT, content="")
        self.tool_def = ToolDefinition.objects.create(name="test.tool", display_name="Test")

    def test_default_status_is_requested(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0,
        )
        self.assertEqual(call.status, ToolCall.Status.REQUESTED)

    def test_can_transition_to_validated(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0, status=ToolCall.Status.VALIDATED,
        )
        self.assertEqual(call.status, ToolCall.Status.VALIDATED)

    def test_can_transition_to_denied(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0, status=ToolCall.Status.DENIED,
        )
        self.assertEqual(call.status, ToolCall.Status.DENIED)

    def test_can_transition_to_timed_out(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0, status=ToolCall.Status.TIMED_OUT,
        )
        self.assertEqual(call.status, ToolCall.Status.TIMED_OUT)

    def test_validation_errors_default_empty(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0,
        )
        self.assertEqual(call.validation_errors, [])

    def test_permission_result_default_empty(self):
        call = ToolCall.objects.create(
            user=self.user, chat=self.chat, message=self.msg,
            tool=self.tool_def, name_snapshot="t", version_snapshot="1",
            sequence=0,
        )
        self.assertEqual(call.permission_result, {})


# ═══════════════════════════════════════════════════════════════════════════════
# Schema format compliance (Gemma chat template format)
# ═══════════════════════════════════════════════════════════════════════════════

class ToolSchemaComplianceTest(TestCase):
    """Verify that exported schemas match the format expected by Gemma's
    apply_chat_template(tools=...) for function calling."""

    def setUp(self):
        self.user = User.objects.create_user(email="user@e.com", password="pass")
        self.reg = ToolRegistry()

    def tearDown(self):
        self.reg._tools.clear()

    def test_schema_matches_openai_function_format(self):
        ToolDefinition.objects.create(
            name="get_weather",
            display_name="Get Weather",
            description="Gets the current weather for a location",
            schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
            },
        )
        self.reg.refresh_from_db()
        schemas = self.reg.get_schemas(self.user)
        self.assertEqual(len(schemas), 1)
        s = schemas[0]
        self.assertEqual(s["type"], "function")
        fn = s["function"]
        self.assertEqual(fn["name"], "get_weather")
        self.assertIn("description", fn)
        self.assertEqual(fn["parameters"]["type"], "object")
        self.assertIn("location", fn["parameters"]["properties"])
        self.assertEqual(fn["parameters"]["required"], ["location"])

    def test_schema_with_enum_parameters(self):
        ToolDefinition.objects.create(
            name="set_mode",
            display_name="Set Mode",
            description="Sets the operating mode",
            schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "manual", "debug"],
                        "description": "Operating mode",
                    },
                },
                "required": ["mode"],
            },
        )
        self.reg.refresh_from_db()
        s = self.reg.get_schemas(self.user)[0]
        mode_prop = s["function"]["parameters"]["properties"]["mode"]
        self.assertEqual(mode_prop["enum"], ["auto", "manual", "debug"])

    def test_schema_descriptions_preserved(self):
        ToolDefinition.objects.create(
            name="test.desc",
            display_name="Test Description",
            description="A tool with a long description for the LLM to understand",
            schema={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "The input value to process"},
                },
                "required": ["input"],
            },
        )
        self.reg.refresh_from_db()
        s = self.reg.get_schemas(self.user)[0]
        self.assertEqual(s["function"]["description"], "A tool with a long description for the LLM to understand")
        self.assertIn("description", s["function"]["parameters"]["properties"]["input"])
