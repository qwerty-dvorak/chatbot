import json
import logging
import time
from typing import Any

from django.conf import settings

from .errors import LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError
from .token_usage import record_token_usage

logger = logging.getLogger(__name__)


def _openai_compatible_model(model: str) -> str:
    """Tell LiteLLM to use its OpenAI provider for our vLLM endpoints."""
    if model.startswith("openai/"):
        return model
    return f"openai/{model}"


def _truncate_payload(messages: list, max_chars: int = 2000) -> str:
    dump = json.dumps(messages, default=str)
    if len(dump) <= max_chars:
        return dump
    return dump[:max_chars] + f"... (truncated, {len(dump)} total)"


def _debug_log(messages: list, model: str, kwargs: dict):
    if not getattr(settings, "CHAT_DEBUG", False):
        return
    n_images = sum(
        1 for m in messages
        if isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    )
    n_files = sum(
        1 for m in messages
        if isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "text" and part["text"].startswith("--- ")
    )
    logger.info(
        "[CHAT_DEBUG] model=%s messages=%d images=%d files=%d tools=%s payload=%s",
        model,
        len(messages),
        n_images,
        n_files,
        bool(kwargs.get("tools")),
        _truncate_payload(messages),
    )


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

    def _has_multimodal(self, messages: list) -> bool:
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    def _select_model(self, messages: list) -> str:
        if self._has_multimodal(messages):
            logger.debug("Detected multimodal content, using vision_model=%s", self.vision_model)
            return _openai_compatible_model(self.vision_model)
        return _openai_compatible_model(self.chat_model)

    def _extra_body(self, thinking_mode: bool | None = None) -> dict | None:
        if thinking_mode is not None:
            extra_body = {"chat_template_kwargs": {"enable_thinking": thinking_mode}}
            if thinking_mode:
                extra_body["skip_special_tokens"] = False
            return extra_body
        if getattr(settings, "CHAT_REASONING_ENABLED", False):
            return {
                "chat_template_kwargs": {"enable_thinking": True},
                "skip_special_tokens": False,
            }
        return None

    def chat_completion(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        model = self._select_model(messages)
        _debug_log(messages, model, kwargs)
        start = time.time()
        try:
            completion = self._get_client()
            call_kwargs = dict(
                model=model,
                messages=messages,
                max_tokens=kwargs.get("max_tokens", settings.CHAT_RESPONSE_MAX_TOKENS),
                temperature=kwargs.get("temperature", 0.7),
                stream=False,
                api_base=self.base_url,
                api_key=self.api_key,
            )
            extra_body = self._extra_body(kwargs.get("thinking_mode"))
            if extra_body:
                call_kwargs["extra_body"] = extra_body
            response = completion(**call_kwargs)
            duration = time.time() - start
            self._log_usage(response, "chat", duration)
            msg = response.choices[0].message
            result = {
                "content": msg.content or "",
                "finish_reason": response.choices[0].finish_reason,
                "usage": dict(response.usage) if response.usage else {},
            }
            reasoning = getattr(msg, "reasoning", None)
            if reasoning:
                result["reasoning"] = reasoning
            return result
        except Exception as e:
            raise self._normalize_error(e)

    def chat_completion_stream(self, messages: list[dict[str, str]], **kwargs):
        model = self._select_model(messages)
        _debug_log(messages, model, kwargs)
        start = time.time()
        try:
            completion = self._get_client()
            call_kwargs = dict(
                model=model,
                messages=messages,
                max_tokens=kwargs.get("max_tokens", settings.CHAT_RESPONSE_MAX_TOKENS),
                temperature=kwargs.get("temperature", 0.7),
                stream=True,
                stream_options={"include_usage": True},
                api_base=self.base_url,
                api_key=self.api_key,
            )
            extra_body = self._extra_body(kwargs.get("thinking_mode"))
            if extra_body:
                call_kwargs["extra_body"] = extra_body
            if "tools" in kwargs:
                call_kwargs["tools"] = kwargs["tools"]
            if "tool_choice" in kwargs:
                call_kwargs["tool_choice"] = kwargs["tool_choice"]
            response = completion(**call_kwargs)
            last_chunk = None
            for chunk in response:
                last_chunk = chunk
                yield chunk
            if last_chunk:
                duration = time.time() - start
                self._log_usage(last_chunk, "chat_stream", duration)
                logger.info("[TIMING] streaming_llm=%.3fs model=%s", duration, model)
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
