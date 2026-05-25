import json
from typing import Any

from .models import ToolDefinition


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def get_enabled(self) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.is_enabled]

    def get_schemas(self, user=None) -> list[dict[str, Any]]:
        schemas = []
        for tool in self.get_enabled():
            if not self._is_permitted(tool, user):
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.schema or {},
                },
            })
        return schemas

    def _is_permitted(self, tool: ToolDefinition, user) -> bool:
        from .permissions import check_tool_permission
        return check_tool_permission(tool, user)

    def refresh_from_db(self):
        self._tools.clear()
        for tool in ToolDefinition.objects.all():
            self._tools[tool.name] = tool


registry = ToolRegistry()
