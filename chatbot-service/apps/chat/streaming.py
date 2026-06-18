import json
import logging
import time

from django.http import StreamingHttpResponse

from apps.chat.models import Message, MessageDelta
from apps.llm.clients import LiteLLMClient
from apps.llm.streaming import StreamHandler
from apps.tools.registry import registry

logger = logging.getLogger(__name__)


def stream_chat_response(message: Message, user) -> StreamingHttpResponse:
    chat = message.chat
    message.status = Message.Status.STREAMING
    message.save(update_fields=["status"])

    from .context import ContextBuilder
    builder = ContextBuilder(chat, user)

    parent_msg = (
        Message.objects
        .filter(chat=chat, role=Message.Role.USER, status=Message.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )
    context_messages = builder.build(parent_msg.content if parent_msg else "", parent_msg)

    client      = LiteLLMClient()
    tool_schemas = registry.get_schemas(user)
    thinking_mode = bool((message.metadata or {}).get("thinking_mode"))

    response = StreamingHttpResponse(
        _event_stream(
            message,
            user,
            client,
            context_messages,
            tool_schemas,
            thinking_mode=thinking_mode,
        ),
        content_type="text/event-stream",
    )
    response["Cache-Control"]     = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _event_stream(
    message,
    user,
    client,
    context_messages,
    tool_schemas,
    thinking_mode=False,
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
        kwargs = {"thinking_mode": thinking_mode}
        if tool_schemas:
            kwargs["tools"] = tool_schemas

        for chunk in client.chat_completion_stream(context_messages, **kwargs):
            for event in handler1.handle_chunk(chunk):
                yield f"data: {json.dumps(event)}\n\n"

        if not handler1.completed_tool_calls:
            duration = time.time() - _start
            logger.info("[TIMING] chat_stream_total=%.3fs tools=0", duration)
            return

        tool_result_messages: list[dict] = []

        for tc in handler1.completed_tool_calls:
            result_content = _execute_tool(tc, message, user)

            yield f"data: {json.dumps({'type': 'tool_result', 'tool_call_id': tc['id'], 'name': tc['name'], 'content': result_content})}\n\n"

            tool_result_messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      result_content,
            })

        assistant_tc_msg = {
            "role":    "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id":       tc["id"],
                    "type":     "function",
                    "function": {"name": tc["name"], "arguments": tc["args"]},
                }
                for tc in handler1.completed_tool_calls
            ],
        }
        continuation = context_messages + [assistant_tc_msg] + tool_result_messages

        message.content = ""
        message.status  = Message.Status.STREAMING
        message.save(update_fields=["content", "status"])

        handler2 = StreamHandler(message)
        handler2.sequence = handler1.sequence

        for chunk in client.chat_completion_stream(
            continuation,
            thinking_mode=thinking_mode,
        ):
            for event in handler2.handle_chunk(chunk):
                yield f"data: {json.dumps(event)}\n\n"

        duration = time.time() - _start
        logger.info("[TIMING] chat_stream_total=%.3fs tools=%d", duration, len(handler1.completed_tool_calls))

    except Exception as exc:
        logger.exception("Streaming failed")
        message.status = Message.Status.FAILED
        message.save(update_fields=["status"])
        yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"


def _execute_tool(tc: dict, message: Message, user) -> str:
    from apps.tools.models import ToolCall as ToolCallModel, ToolDefinition
    from apps.tools.executor import ToolExecutor

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
