import json
import logging

from django.conf import settings
from django.http import StreamingHttpResponse

from apps.chat.models import Message, MessageDelta
from apps.llm.clients import LiteLLMClient
from apps.llm.streaming import StreamHandler
from apps.tools.executor import ToolExecutor
from apps.tools.models import ToolCall, ToolDefinition
from apps.tools.registry import registry

logger = logging.getLogger(__name__)


def stream_chat_response(message: Message, user) -> StreamingHttpResponse:
    chat = message.chat
    message.status = Message.Status.STREAMING
    message.save(update_fields=["status"])

    from .context import ContextBuilder
    builder = ContextBuilder(chat, user)
    context_messages = builder.build("")

    parent_msg = Message.objects.filter(chat=chat, status=Message.Status.COMPLETED).last()
    if parent_msg and parent_msg.role == Message.Role.USER:
        context_messages[-1]["content"] = parent_msg.content

    client = LiteLLMClient()
    handler = StreamHandler(message)

    def event_stream():
        try:
            tool_schemas = registry.get_schemas(user)
            kwargs = {}
            if tool_schemas:
                kwargs["tools"] = tool_schemas

            for chunk in client.chat_completion_stream(context_messages, **kwargs):
                for event in handler.handle_chunk(chunk):
                    yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error(f"Streaming failed: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            message.status = Message.Status.FAILED
            message.save(update_fields=["status"])

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
