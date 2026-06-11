import json
import logging
from typing import Any, Generator

from apps.chat.models import Message, MessageDelta

logger = logging.getLogger(__name__)


class StreamHandler:
    """
    Processes LiteLLM streaming chunks and converts them to SSE events.

    Supports:
      - Text deltas (choices[0].delta.content)
      - Reasoning / thinking deltas (choices[0].delta.reasoning_content)
      - Tool call deltas (choices[0].delta.tool_calls)
      - Finish reasons: "stop", "tool_calls"

    Reasoning content is accumulated separately and stored in message metadata
    under the "reasoning" key when streaming completes.
    """

    def __init__(self, message: Message):
        self.message   = message
        self.accumulated_content = ""
        self.accumulated_reasoning = ""
        self._tc_acc: dict[int, dict] = {}
        self.completed_tool_calls: list[dict] = []

        from django.db.models import Max
        existing = MessageDelta.objects.filter(message=message).aggregate(
            mx=Max("sequence")
        )["mx"]
        self.sequence = (existing + 1) if existing is not None else 0

    # ── public interface ───────────────────────────────────────────────────────

    def handle_chunk(self, chunk: Any) -> Generator[dict[str, Any], None, None]:
        if not chunk.choices:
            return

        choice       = chunk.choices[0]
        delta        = getattr(choice, "delta", None)
        finish       = getattr(choice, "finish_reason", None)

        if delta:
            content           = getattr(delta, "content", None)
            reasoning_content = getattr(delta, "reasoning_content", None)
            tool_calls        = getattr(delta, "tool_calls", None)

            if reasoning_content:
                yield from self._handle_reasoning(reasoning_content)

            if content:
                yield from self._handle_text(content)

            if tool_calls:
                for tc in tool_calls:
                    self._accumulate_tc(tc)

        if finish == "stop":
            yield from self._handle_stop()
        elif finish == "tool_calls":
            yield from self._handle_tool_calls_finish()

    # ── private helpers ────────────────────────────────────────────────────────

    def _accumulate_tc(self, tc: Any) -> None:
        idx = getattr(tc, "index", 0) or 0
        if idx not in self._tc_acc:
            self._tc_acc[idx] = {"id": "", "name": "", "args": ""}

        if tc_id := getattr(tc, "id", None):
            self._tc_acc[idx]["id"] = tc_id

        fn = getattr(tc, "function", None)
        if fn:
            fn_name = getattr(fn, "name", None) or ""
            fn_args = getattr(fn, "arguments", None) or ""
            self._tc_acc[idx]["name"] = self._tc_acc[idx]["name"] or fn_name
            self._tc_acc[idx]["args"] += fn_args

    def _handle_reasoning(self, content: str) -> Generator[dict, None, None]:
        self.accumulated_reasoning += content
        MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.TEXT,
            content=content,
        )
        self.sequence += 1
        yield {"type": "reasoning", "content": content}

    def _handle_text(self, content: str) -> Generator[dict, None, None]:
        self.accumulated_content += content
        MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.TEXT,
            content=content,
        )
        self.sequence += 1
        yield {"type": "text", "content": content}

    def _handle_stop(self) -> Generator[dict, None, None]:
        MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.DONE,
            content="",
        )
        self.sequence += 1

        self.message.content = self.accumulated_content
        self.message.status  = Message.Status.COMPLETED
        if self.accumulated_reasoning:
            metadata = dict(self.message.metadata or {})
            metadata["reasoning"] = self.accumulated_reasoning
            self.message.metadata = metadata
            self.message.save(update_fields=["content", "status", "metadata"])
        else:
            self.message.save(update_fields=["content", "status"])
        yield {"type": "done"}

    def _handle_tool_calls_finish(self) -> Generator[dict, None, None]:
        self.completed_tool_calls = []

        for idx in sorted(self._tc_acc):
            tc = self._tc_acc[idx]
            try:
                args_dict = json.loads(tc["args"]) if tc["args"] else {}
            except json.JSONDecodeError:
                args_dict = {}

            self.completed_tool_calls.append({
                "id":   tc["id"] or f"call_{idx}",
                "name": tc["name"],
                "args": tc["args"],
                "args_dict": args_dict,
            })

            MessageDelta.objects.create(
                message=self.message,
                sequence=self.sequence,
                delta_type=MessageDelta.DeltaType.TOOL_CALL,
                content=json.dumps({"id": tc["id"], "name": tc["name"],
                                    "arguments": tc["args"]}),
            )
            self.sequence += 1

            yield {
                "type": "tool_call",
                "tool_call": {
                    "id":       tc["id"],
                    "name":     tc["name"],
                    "arguments": args_dict,
                },
            }
