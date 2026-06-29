import json
import logging
from typing import Any, Generator

from apps.chat.models import Message, MessageDelta

logger = logging.getLogger(__name__)


class ChannelContentParser:
    """Split Gemma channel markers carried in ordinary content deltas."""

    THOUGHT_STARTS = ("<|channel>thought", "<|channel|>thought")
    THOUGHT_ENDS = ("<channel|>", "<|end|>")
    FINAL_STARTS = ("<|channel>final", "<|channel|>final")

    def __init__(self):
        self.mode = "text"
        self.buffer = ""

    def feed(self, content: str) -> list[tuple[str, str]]:
        self.buffer += content
        return self._drain(final=False)

    def finish(self) -> list[tuple[str, str]]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> list[tuple[str, str]]:
        parts = []
        while self.buffer:
            if self.mode == "text":
                marker = self._first_marker(self.THOUGHT_STARTS)
                final_marker = self._first_marker(self.FINAL_STARTS)
                if final_marker and (not marker or final_marker[0] < marker[0]):
                    index, token = final_marker
                    if index:
                        parts.append(("text", self.buffer[:index]))
                    self.buffer = self.buffer[index + len(token):]
                    continue
                if marker:
                    index, token = marker
                    if index:
                        parts.append(("text", self.buffer[:index]))
                    self.buffer = self.buffer[index + len(token):]
                    self.mode = "reasoning"
                    continue
                safe = self._safe_length(self.THOUGHT_STARTS + self.FINAL_STARTS, final)
                if safe:
                    parts.append(("text", self.buffer[:safe]))
                    self.buffer = self.buffer[safe:]
                break

            marker = self._first_marker(self.THOUGHT_ENDS)
            if marker:
                index, token = marker
                if index:
                    parts.append(("reasoning", self.buffer[:index]))
                self.buffer = self.buffer[index + len(token):]
                self.mode = "text"
                continue
            safe = self._safe_length(self.THOUGHT_ENDS, final)
            if safe:
                parts.append(("reasoning", self.buffer[:safe]))
                self.buffer = self.buffer[safe:]
            break
        return [(kind, text) for kind, text in parts if text]

    def _first_marker(self, markers: tuple[str, ...]) -> tuple[int, str] | None:
        matches = [(self.buffer.find(marker), marker) for marker in markers]
        matches = [match for match in matches if match[0] >= 0]
        return min(matches, default=None, key=lambda match: match[0])

    def _safe_length(self, markers: tuple[str, ...], final: bool) -> int:
        if final:
            return len(self.buffer)
        held = 0
        for marker in markers:
            for length in range(1, min(len(marker), len(self.buffer)) + 1):
                if self.buffer.endswith(marker[:length]):
                    held = max(held, length)
        return len(self.buffer) - held


class StreamHandler:
    """
    Processes streaming chunks from the chat completion API and converts them to SSE events.

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
        self.channel_parser = ChannelContentParser()
        self._tc_acc: dict[int, dict] = {}
        self.completed_tool_calls: list[dict] = []

        from django.db.models import Max
        existing = MessageDelta.objects.filter(message=message).aggregate(
            mx=Max("sequence")
        )["mx"]
        self.sequence = (existing + 1) if existing is not None else 0

    # ── public interface ───────────────────────────────────────────────────────

    def handle_chunk(self, chunk: dict) -> Generator[dict[str, Any], None, None]:
        choices = chunk.get("choices")
        if not choices:
            return

        choice       = choices[0]
        delta        = choice.get("delta")
        finish       = choice.get("finish_reason")

        if delta:
            content           = delta.get("content")
            reasoning_content = delta.get("reasoning_content")
            reasoning         = delta.get("reasoning")
            tool_calls        = delta.get("tool_calls")

            if reasoning_content:
                yield from self._handle_reasoning(reasoning_content)
            elif reasoning:
                yield from self._handle_reasoning(reasoning)

            if content:
                yield from self._handle_content(content)

            if tool_calls:
                for tc in tool_calls:
                    self._accumulate_tc(tc)

        if finish == "stop":
            yield from self._handle_stop()
        elif finish == "tool_calls":
            yield from self._handle_tool_calls_finish()

    # ── private helpers ────────────────────────────────────────────────────────

    def _accumulate_tc(self, tc: dict) -> None:
        idx = tc.get("index", 0) or 0
        if idx not in self._tc_acc:
            self._tc_acc[idx] = {"id": "", "name": "", "args": ""}

        if tc_id := tc.get("id"):
            self._tc_acc[idx]["id"] = tc_id

        fn = tc.get("function")
        if fn:
            fn_name = fn.get("name", "") or ""
            fn_args = fn.get("arguments", "") or ""
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

    def _handle_content(self, content: str) -> Generator[dict, None, None]:
        for kind, parsed in self.channel_parser.feed(content):
            if kind == "reasoning":
                yield from self._handle_reasoning(parsed)
            else:
                yield from self._handle_text(parsed)

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
        for kind, parsed in self.channel_parser.finish():
            if kind == "reasoning":
                yield from self._handle_reasoning(parsed)
            else:
                yield from self._handle_text(parsed)

        MessageDelta.objects.create(
            message=self.message,
            sequence=self.sequence,
            delta_type=MessageDelta.DeltaType.DONE,
            content="",
        )
        self.sequence += 1

        self.message.content = self.accumulated_content.strip()
        self.message.status  = Message.Status.COMPLETED
        if self.accumulated_reasoning:
            metadata = dict(self.message.metadata or {})
            metadata["reasoning"] = self.accumulated_reasoning.strip()
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
