"""SSE streaming of chat responses via server-sent events."""

import json
import logging
import re
import time

from django.http import StreamingHttpResponse

from apps.chat.models import Message
from apps.llm.clients import ChatClient
from apps.llm.streaming import StreamHandler
from apps.tools.executor import ToolExecutor
from apps.tools.models import ToolCall as ToolCallModel
from apps.tools.models import ToolDefinition
from apps.tools.registry import registry

from .context import ContextBuilder

# Matches call:name{args} optionally wrapped in <|tool_call|> markers.
# The LLM may emit only the opening marker, both, or neither.
TOOL_CALL_TEXT_RE = re.compile(
    r"(?:<\|tool_call\|>\s*)?call:(\w+(?:\.\w+)*)\{([^}]*)\}(?:\s*<\|tool_call\|>)?"
)


def _unjson_args(raw: str) -> dict:
    """Parse LLM args like `{document_id:"filename.pdf"}` that aren't valid JSON."""
    s = raw.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    result = {}
    # Match key:"value" or key:'value' or key:value pairs, comma separated
    for pair in re.findall(r"""(?:^|,\s*)(\w+)\s*:\s*(?:"([^"]*)"|'([^']*)'|(\S+))""", s):
        key = pair[0]
        val = pair[1] or pair[2] or pair[3]
        result[key] = val
    return result


def _parse_text_tool_calls(content: str) -> tuple[str, list[dict]]:
    """Parse `call:` format tool calls embedded in LLM text content.

    Returns (clean_text, tool_calls) where clean_text has tool call markers removed.
    """
    tool_calls: list[dict] = []
    clean_parts: list[str] = []
    last_end = 0

    for m in TOOL_CALL_TEXT_RE.finditer(content):
        clean_parts.append(content[last_end : m.start()])
        name = m.group(1)
        args_raw = m.group(2).replace('<|"|>', '"').replace("<|'|>", '"')
        if args_raw.startswith('"'):
            args_raw = "{" + args_raw + "}"
        try:
            args_dict = json.loads(args_raw) if args_raw.startswith("{") else _unjson_args(args_raw)
        except json.JSONDecodeError:
            args_dict = _unjson_args(args_raw)
        tool_calls.append({
            "id": f"call_text_{len(tool_calls)}",
            "name": name,
            "args": args_raw,
            "args_dict": args_dict,
        })
        last_end = m.end()

    clean_parts.append(content[last_end:])
    clean_text = "".join(clean_parts).strip()
    return clean_text, tool_calls

logger = logging.getLogger(__name__)


def stream_chat_response(message: Message, user) -> StreamingHttpResponse:
    chat = message.chat
    message.status = Message.Status.STREAMING
    message.save(update_fields=["status"])

    builder = ContextBuilder(chat, user)

    parent_msg = (
        Message.objects
        .filter(chat=chat, role=Message.Role.USER, status=Message.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )
    context_messages, rag_log = builder.build(parent_msg.content if parent_msg else "", parent_msg)

    if rag_log:
        if parent_msg:
            parent_msg.metadata["rag_search_log"] = rag_log
            parent_msg.save(update_fields=["metadata"])
        if rag_log.get("rag_used"):
            message.metadata["rag_used"] = True
            message.save(update_fields=["metadata"])

    client      = ChatClient()
    tool_schemas = registry.get_schemas(user)
    thinking_mode = bool((message.metadata or {}).get("thinking_mode"))
    lora_adapter = (message.metadata or {}).get("lora_adapter") or (chat.metadata or {}).get("lora_adapter") or ""

    response = StreamingHttpResponse(
        _event_stream(
            message,
            user,
            client,
            context_messages,
            tool_schemas,
            thinking_mode=thinking_mode,
            lora_adapter=lora_adapter or None,
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"]     = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _event_stream(  # noqa: C901, PLR0912, PLR0913
    message,
    user,
    client,
    context_messages,
    tool_schemas,
    thinking_mode=False,
    lora_adapter=None,
):
    """
    Two-round streaming:
      Round 1 — LLM call with tools.  May produce text OR a tool_call.
      If tool_calls: execute each tool, emit tool_result events, then
      Round 2 — LLM continuation with tool results → final text → done.
    """
    _start = time.time()
    try:
        handler1 = StreamHandler(message)
        kwargs = {"thinking_mode": thinking_mode, "lora_adapter": lora_adapter}
        if tool_schemas:
            kwargs["tools"] = tool_schemas

        for chunk in client.chat_completion_stream(context_messages, **kwargs):
            if _is_cancelled(message):
                yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                return
            for event in handler1.handle_chunk(chunk):
                yield f"data: {json.dumps(event)}\n\n"

        tool_calls = list(handler1.completed_tool_calls)
        text_tool_calls: list[dict] = []
        clean_text = None

        if not tool_calls:
            text_content = handler1.accumulated_content
            clean_text, text_tool_calls = _parse_text_tool_calls(text_content)
            if text_tool_calls:
                tool_calls = text_tool_calls
                message.content = clean_text
                message.save(update_fields=["content"])
                for tc in text_tool_calls:
                    yield f"data: {json.dumps({'type': 'tool_call', 'tool_call': {'id': tc['id'], 'name': tc['name'], 'arguments': tc.get('args_dict', {})}})}\n\n"

        if not tool_calls:
            duration = time.time() - _start
            logger.info("[TIMING] chat_stream_total=%.3fs tools=0", duration)
            return

        tool_result_messages: list[dict] = []

        for tc in tool_calls:
            result_content = _execute_tool(tc, message, user)

            yield f"data: {json.dumps({'type': 'tool_result', 'tool_call_id': tc['id'], 'name': tc['name'], 'content': result_content})}\n\n"

            tool_result_messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result_content,
            })

        assistant_content = clean_text if text_tool_calls else None
        assistant_tc_msg = {
            "role":    "assistant",
            "content": assistant_content,
            "tool_calls": [
                {
                    "id":       tc["id"],
                    "type":     "function",
                    "function": {"name": tc["name"], "arguments": tc["args"]},
                }
                for tc in tool_calls
            ],
        }
        continuation = [*context_messages, assistant_tc_msg, *tool_result_messages]

        message.content = ""
        message.status  = Message.Status.STREAMING
        message.save(update_fields=["content", "status"])

        handler2 = StreamHandler(message)
        handler2.sequence = handler1.sequence

        for chunk in client.chat_completion_stream(
            continuation,
            thinking_mode=thinking_mode,
            lora_adapter=lora_adapter,
        ):
            if _is_cancelled(message):
                yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                return
            for event in handler2.handle_chunk(chunk):
                yield f"data: {json.dumps(event)}\n\n"

        duration = time.time() - _start
        logger.info("[TIMING] chat_stream_total=%.3fs tools=%d", duration, len(handler1.completed_tool_calls))

    except Exception as exc:
        logger.exception("Streaming failed")
        message.status = Message.Status.FAILED
        message.save(update_fields=["status"])
        yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"


def _is_cancelled(message: Message) -> bool:
    return Message.objects.filter(id=message.id, status=Message.Status.CANCELLED).exists()


def _execute_tool(tc: dict, message: Message, user) -> str:
    name     = tc["name"]
    args     = tc["args_dict"]
    tc_id    = tc["id"]

    tool_def = ToolDefinition.objects.filter(name=name, is_enabled=True).first()
    if not tool_def:
        return f"Error: tool '{name}' not found or disabled."

    seq = ToolCallModel.objects.filter(message=message).count()

    tc_record = ToolCallModel.objects.create(
        tool_call_id    = tc_id,
        user            = user,
        chat            = message.chat,
        message         = message,
        tool            = tool_def,
        name_snapshot   = name,
        version_snapshot= tool_def.version,
        status          = ToolCallModel.Status.RUNNING,
        arguments       = args,
        raw_arguments   = {"raw": tc["args"]},
        sequence        = seq,
    )

    executor = ToolExecutor(registry, context={"user": user, "chat": message.chat})
    result   = executor.execute(tc_record)
    return result.content
