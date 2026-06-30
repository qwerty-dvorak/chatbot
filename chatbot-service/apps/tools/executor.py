"""Tool execution engine for built-in tools."""

import datetime
import json
import time
from typing import Any

from .builtin import BUILTIN_TOOLS
from .models import ToolCall, ToolDefinition, ToolExecution, ToolResult


class ToolExecutor:
    def __init__(self, tool_registry, context: dict | None = None) -> None:
        self.registry = tool_registry
        self.context  = context or {}

    def execute(self, tool_call: ToolCall) -> ToolResult:
        tool_def = self.registry.get(tool_call.name_snapshot)

        execution = ToolExecution.objects.create(
            tool_call=tool_call,
            attempt=1,
            input_snapshot=tool_call.arguments,
        )

        if not tool_def:
            execution.status = ToolExecution.Status.FAILED
            execution.error_message = f"Tool {tool_call.name_snapshot} not found in registry"
            execution.completed_at = datetime.datetime.now(tz=datetime.UTC)
            execution.save(update_fields=["status", "error_message", "completed_at"])
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
            execution.completed_at = datetime.datetime.now(tz=datetime.UTC)
            execution.save(update_fields=["status", "duration_ms", "completed_at"])

            return ToolResult.objects.create(
                tool_call=tool_call,
                execution=execution,
                content=result_content,
            )

        except Exception as e:  # noqa: BLE001
            execution.status = ToolExecution.Status.FAILED
            execution.error_message = f"{type(e).__name__}: {e}"
            execution.duration_ms = int((time.time() - start) * 1000)
            execution.completed_at = datetime.datetime.now(tz=datetime.UTC)
            execution.save(update_fields=["status", "error_message", "duration_ms", "completed_at"])

            return ToolResult.objects.create(
                tool_call=tool_call,
                execution=execution,
                content=f"Error executing {tool_call.name_snapshot}: {e}",
            )

    def _run_tool(self, tool_def: ToolDefinition, arguments: dict[str, Any]) -> str:
        handler = BUILTIN_TOOLS.get(tool_def.name)
        if not handler:
            msg = f"No handler registered for tool: {tool_def.name}"
            raise ValueError(msg)

        result = handler(arguments, self.context)
        if isinstance(result, dict):
            return json.dumps(result)
        return str(result)
