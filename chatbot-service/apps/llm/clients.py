"""Chat completion client.

Pure urllib — no external LLM SDK dependency.  Uses the shared endpoint helpers and HTTP
client from this package so URL building and error handling are consistent across
all LLM API clients.
"""

import json
import logging
import time
from typing import Any

from django.conf import settings

from .endpoints import chat_completions_url, models_url, normalize_url
from .errors import (
    LLMConnectionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from .http_client import json_request, json_stream_request

logger = logging.getLogger(__name__)


def _truncate_payload(messages: list, max_chars: int = 2000) -> str:
    dump = json.dumps(messages, default=str)
    if len(dump) <= max_chars:
        return dump
    return dump[:max_chars] + f"... (truncated, {len(dump)} total)"


def _discover_lora_via_api(base_url: str | None = None) -> list[str] | None:
    """Query the vLLM /v1/models endpoint to discover LoRA adapters.

    Returns a list of adapter names (e.g. ['taboo-ship', 'taboo-book']),
    or None if the endpoint cannot be reached (caller should fall back to env var).
    """
    url = models_url(base_url or settings.CHAT_BASE_URL)
    try:
        resp = json_request(url, method="GET")
        models = [m["id"] for m in resp.get("data", [])]
        base = settings.CHAT_MODEL.removeprefix("openai/")
        return [m for m in models if m != base and "/" not in m]
    except Exception:  # noqa: BLE001
        logger.debug("Could not discover LoRAs from %s", url)
        return None


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


class ChatClient:
    """Chat completion client backed by raw urllib requests.

    Talks to any OpenAI-compatible chat endpoint.  Supports streaming, tool
    calling, LoRA adapters, thinking mode, and multimodal messages.
    """

    def __init__(self):
        self.base_url = normalize_url(settings.CHAT_BASE_URL)
        self.api_key = settings.CHAT_API_KEY
        self.chat_model = settings.CHAT_MODEL.removeprefix("openai/")
        self.vision_model = settings.VISION_MODEL.removeprefix("openai/")

    def _has_multimodal(self, messages: list) -> bool:
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    def _select_model(self, messages: list, lora_adapter: str | None = None) -> str:
        if lora_adapter:
            return lora_adapter
        if self._has_multimodal(messages):
            logger.debug("Detected multimodal content, using vision_model=%s", self.vision_model)
            return self.vision_model
        return self.chat_model

    def _build_body(self, model: str, messages: list, stream: bool,
                    **kwargs) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", settings.CHAT_RESPONSE_MAX_TOKENS),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if "tools" in kwargs:
            body["tools"] = kwargs["tools"]
        if "tool_choice" in kwargs:
            body["tool_choice"] = kwargs["tool_choice"]

        thinking_mode = kwargs.get("thinking_mode")
        lora_adapter = kwargs.get("lora_adapter")
        if thinking_mode is not None:
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = thinking_mode
            if thinking_mode:
                body["skip_special_tokens"] = False
        elif getattr(settings, "CHAT_REASONING_ENABLED", False):
            body.setdefault("chat_template_kwargs", {})["enable_thinking"] = True
            body["skip_special_tokens"] = False
        if lora_adapter:
            body["add_lora"] = lora_adapter
        return body

    def chat_completion(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        lora_adapter = kwargs.get("lora_adapter")
        model = self._select_model(messages, lora_adapter=lora_adapter)
        _debug_log(messages, model, kwargs)
        _start = time.time()
        try:
            body = self._build_body(model, messages, stream=False, **kwargs)
            url = chat_completions_url(self.base_url)
            data = json_request(url, body, api_key=self.api_key)
            choice = data["choices"][0]
            msg = choice["message"]
            result = {
                "content": msg.get("content", "") or "",
                "finish_reason": choice.get("finish_reason"),
                "usage": data.get("usage", {}),
            }
            if msg.get("reasoning"):
                result["reasoning"] = msg["reasoning"]
            return result  # noqa: TRY300
        except (LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError):
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMProviderError(str(e), provider="openai")  # noqa: B904

    def chat_completion_stream(self, messages: list[dict[str, str]], **kwargs):
        lora_adapter = kwargs.get("lora_adapter")
        model = self._select_model(messages, lora_adapter=lora_adapter)
        _debug_log(messages, model, kwargs)
        start = time.time()
        try:
            body = self._build_body(model, messages, stream=True, **kwargs)
            url = chat_completions_url(self.base_url)
            resp = json_stream_request(url, body, api_key=self.api_key)
            last_chunk = None
            for line in resp:
                line = line.decode().strip()  # noqa: PLW2901
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    last_chunk = chunk
                    yield chunk
            if last_chunk:
                duration = time.time() - start
                logger.info("[TIMING] streaming_llm=%.3fs model=%s", duration, model)
        except (LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError):
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMProviderError(str(e), provider="openai")  # noqa: B904
