import json
import logging
from typing import Any, Generator

from apps.chat.models import Message, MessageDelta

logger = logging.getLogger(__name__)


class StreamHandler:
    """
    Processes LiteLLM streaming chunks and converts them to SSE events.

    Tool call deltas arrive in pieces across multiple chunks (OpenAI format):
      chunk 1: tool_calls[0].id = "call_xxx", .function.name = "memory.search", .function.arguments = ""
      chunk 2: tool_calls[0].function.arguments = '{"query":'
      chunk 3: tool_calls[0].function.arguments = ' "Python"}'
      finish_reason = "tool_calls"

    This handler accumulates those pieces in self._tc_acc, then exposes
    self.completed_tool_calls when finish_reason="tool_calls" is received.
    The chat streaming layer reads completed_tool_calls to execute the tools
    and continue the conversation.
    """

    def __init__(self, message: Message):
        self.message   = message
        self.sequence  = 0
        self.accumulated_content = ""

        # Accumulator: {delta_index: {id, name, args_str}}
        self._tc_acc: dict[int, dict] = {}
        # Populated when finish_reason == "tool_calls"
        self.completed_tool_calls: list[dict] = []

    # ── public interface ───────────────────────────────────────────────────────

    def handle_chunk(self, chunk: Any) -> Generator[dict[str, Any], None, None]:
        if not chunk.choices:
            return

        choice       = chunk.choices[0]
        delta        = getattr(choice, "delta", None)
        finish       = getattr(choice, "finish_reason", None)

        if delta:
            content    = getattr(delta, "content", None)
            tool_calls = getattr(delta, "tool_calls", None)

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
            # name only appears on first delta; use 'or' to keep first value
            self._tc_acc[idx]["name"] = self._tc_acc[idx]["name"] or fn_name
            self._tc_acc[idx]["args"] += fn_args

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
        self.message.save(update_fields=["content", "status"])
        yield {"type": "done"}

    def _handle_tool_calls_finish(self) -> Generator[dict, None, None]:
        """Convert accumulated deltas to complete tool calls and yield each."""
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
                "args": tc["args"],       # raw string for OpenAI messages
                "args_dict": args_dict,   # parsed for executor
            })

            # Save delta record
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
        # NOTE: no "done" event here — the caller continues with tool results
        # and a second LLM round before emitting done.
