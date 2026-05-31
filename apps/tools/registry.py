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
        from django.conf import settings as django_settings

        # Auto-load from DB on first call (each gunicorn worker starts with empty registry)
        if not self._tools:
            try:
                self.refresh_from_db()
            except Exception:
                return []

        if not getattr(django_settings, "TOOL_CALLS_ENABLED", True):
            return []

        rag_enabled = getattr(django_settings, "RAG_ENABLED", True)
        rag_prefixes = ("rag.", "knowledge.")

        schemas = []
        for tool in self.get_enabled():
            if not rag_enabled and any(tool.name.startswith(p) for p in rag_prefixes):
                continue
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
