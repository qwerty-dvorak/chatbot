import datetime
import json
import time
from typing import Any

from .models import ToolCall, ToolDefinition, ToolExecution, ToolResult


class ToolExecutor:
    def __init__(self, tool_registry):
        self.registry = tool_registry

    def execute(self, tool_call: ToolCall) -> ToolResult:
        tool_def = self.registry.get(tool_call.name_snapshot)

        execution = ToolExecution.objects.create(
            tool_call=tool_call,
            attempt=1,
            input_snapshot=tool_call.arguments,
        )

        if not tool_def:
            execution.status = ToolExecution.Status.FAILED
            execution.error_type = "ToolNotFound"
            execution.error_message = f"Tool {tool_call.name_snapshot} not found in registry"
            execution.completed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            execution.save(update_fields=["status", "error_type", "error_message", "completed_at"])
            return ToolResult.objects.create(
                tool_call=tool_call,
                execution=execution,
                content=f"Error: Tool {tool_call.name_snapshot} is not available.",
            )

        start = time.time()
        try:
            result_content = self._run_tool(tool_def, tool_call.arguments)
            execution.status = ToolExecution.Status.SUCCEEDED
            execution.duration_ms = int((time.time() - start) * 1000)
            execution.completed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            execution.save(update_fields=["status", "duration_ms", "completed_at"])

            tool_result = ToolResult.objects.create(
                tool_call=tool_call,
                execution=execution,
                content=result_content,
            )
            return tool_result

        except Exception as e:
            execution.status = ToolExecution.Status.FAILED
            execution.error_type = type(e).__name__
            execution.error_message = str(e)
            execution.duration_ms = int((time.time() - start) * 1000)
            execution.completed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            execution.save(update_fields=["status", "error_type", "error_message", "duration_ms", "completed_at"])

            tool_result = ToolResult.objects.create(
                tool_call=tool_call,
                execution=execution,
                content=f"Error executing {tool_call.name_snapshot}: {e}",
            )
            return tool_result

    def _run_tool(self, tool_def: ToolDefinition, arguments: dict[str, Any]) -> str:
        from .builtin import BUILTIN_TOOLS

        handler = BUILTIN_TOOLS.get(tool_def.name)
        if not handler:
            raise ValueError(f"No handler registered for tool: {tool_def.name}")

        result = handler(arguments)
        if isinstance(result, dict):
            return json.dumps(result)
        return str(result)
