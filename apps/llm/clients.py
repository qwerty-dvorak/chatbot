import logging
import time
from typing import Any

from django.conf import settings

from .errors import LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError
from .token_usage import record_token_usage

logger = logging.getLogger(__name__)


class LiteLLMClient:
    def __init__(self):
        self.base_url = settings.CHAT_BASE_URL
        self.api_key = settings.CHAT_API_KEY
        self.chat_model = settings.CHAT_MODEL
        self.vision_model = settings.VISION_MODEL

    def _get_client(self):
        try:
            from litellm import completion
            return completion
        except ImportError:
            raise LLMProviderError("litellm is not installed")

    def chat_completion(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        start = time.time()
        try:
            completion = self._get_client()
            response = completion(
                model=self.chat_model,
                messages=messages,
                max_tokens=kwargs.get("max_tokens", settings.CHAT_RESPONSE_MAX_TOKENS),
                temperature=kwargs.get("temperature", 0.7),
                stream=False,
                api_base=self.base_url,
                api_key=self.api_key,
            )
            duration = time.time() - start
            self._log_usage(response, "chat", duration)
            return {
                "content": response.choices[0].message.content or "",
                "finish_reason": response.choices[0].finish_reason,
                "usage": dict(response.usage) if response.usage else {},
            }
        except Exception as e:
            raise self._normalize_error(e)

    def chat_completion_stream(self, messages: list[dict[str, str]], **kwargs):
        try:
            completion = self._get_client()
            response = completion(
                model=self.chat_model,
                messages=messages,
                max_tokens=kwargs.get("max_tokens", settings.CHAT_RESPONSE_MAX_TOKENS),
                temperature=kwargs.get("temperature", 0.7),
                stream=True,
                stream_options={"include_usage": True},
                api_base=self.base_url,
                api_key=self.api_key,
            )
            for chunk in response:
                yield chunk
        except Exception as e:
            raise self._normalize_error(e)

    def _normalize_error(self, error: Exception) -> Exception:
        error_str = str(error).lower()
        if "timeout" in error_str or "timed out" in error_str:
            return LLMTimeoutError(str(error))
        if "rate limit" in error_str or "too many requests" in error_str:
            return LLMRateLimitError(str(error))
        if "authentication" in error_str or "unauthorized" in error_str or "api key" in error_str:
            return LLMProviderError(str(error), provider="litellm", status_code=401)
        if "connection" in error_str:
            return LLMConnectionError(str(error))
        return LLMProviderError(str(error), provider="litellm")

    def _log_usage(self, response, operation: str, duration: float):
        try:
            usage = getattr(response, "usage", None)
            if usage:
                record_token_usage(
                    operation=operation,
                    model=getattr(response, "model", self.chat_model),
                    provider="litellm",
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    metadata={"duration_s": round(duration, 3)},
                )
        except Exception as e:
            logger.warning(f"Failed to log token usage: {e}")


class FakeChunk:
    def __init__(self, content=None, finish_reason=None, tool_calls=None):
        self.choices = [FakeChoice(content=content, finish_reason=finish_reason, tool_calls=tool_calls)]


class FakeChoice:
    def __init__(self, content=None, finish_reason=None, tool_calls=None):
        self.delta = FakeDelta(content=content, tool_calls=tool_calls)
        self.finish_reason = finish_reason
        self.index = 0


class FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeLLMClient:
    def chat_completion(self, messages, **kwargs):
        return {
            "content": "This is a fake response for testing.",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    def chat_completion_stream(self, messages, **kwargs):
        words = ["This ", "is ", "a ", "fake ", "stream."]
        for word in words:
            yield FakeChunk(content=word)

        yield FakeChunk(finish_reason="stop")
