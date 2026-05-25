import json
import logging
from typing import Any, Generator

from apps.chat.models import Message, MessageDelta
from apps.tools.models import ToolCall, ToolDefinition
from apps.tools.registry import registry

logger = logging.getLogger(__name__)


class StreamHandler:
    def __init__(self, message: Message):
        self.message = message
        self.sequence = 0
        self.accumulated_content = ""

    def handle_chunk(self, chunk: Any) -> Generator[dict[str, Any], None, None]:
        delta = getattr(chunk.choices[0], "delta", None) if chunk.choices else None
        if not delta:
            return

        content = getattr(delta, "content", None)
        tool_calls = getattr(delta, "tool_calls", None)

        if content:
            yield from self._handle_text(content)

        if tool_calls:
            for tc in tool_calls:
                yield from self._handle_tool_call(tc)

        finish_reason = getattr(chunk.choices[0], "finish_reason", None)
        if finish_reason == "stop":
            yield from self._handle_done()
        elif finish_reason == "tool_calls":
            yield from self._handle_done()

    def _handle_text(self, content: str) -> Generator[dict[str, Any], None, None]:
        self.accumulated_content += content
        delta = MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.TEXT,
            content=content,
        )
        self.sequence += 1
        yield {"type": "text", "content": content}

    def _handle_tool_call(self, tc: Any) -> Generator[dict[str, Any], None, None]:
        delta = MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.TOOL_CALL,
            content=json.dumps({"id": tc.id, "function": tc.function}),
        )
        self.sequence += 1
        yield {"type": "tool_call", "tool_call": {"id": tc.id, "function": tc.function}}

    def _handle_done(self) -> Generator[dict[str, Any], None, None]:
        MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.DONE,
            content="",
        )
        self.sequence += 1
        self.message.content = self.accumulated_content
        self.message.status = Message.Status.COMPLETED
        self.message.save(update_fields=["content", "status"])
        yield {"type": "done"}

    def persist_tool_call(self, tool_call: ToolCall):
        self.message.tool_invocations.setdefault("calls", [])
        self.message.tool_invocations["calls"].append({
            "id": str(tool_call.id),
            "name": tool_call.name_snapshot,
            "status": tool_call.status,
        })
        self.message.save(update_fields=["tool_invocations"])
