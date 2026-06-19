import json
import logging
import time
import urllib.request
from typing import Any

from django.conf import settings

from .errors import LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError

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
    url = (base_url or settings.CHAT_BASE_URL).rstrip("/v1").rstrip("/") + "/v1/models"
    import urllib.request, json
    req = urllib.request.Request(url, headers={"User-Agent": "opencode/1.0"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        models = [m["id"] for m in resp.get("data", [])]
        base = settings.CHAT_MODEL.removeprefix("openai/")
        return [m for m in models if m != base and "/" not in m]
    except Exception:
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


class LiteLLMClient:
    def __init__(self):
        self.base_url = settings.CHAT_BASE_URL.rstrip("/v1").rstrip("/")
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

    def _request(self, body: dict, stream: bool = False):
        url = f"{self.base_url}/v1/chat/completions"
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "opencode/1.0",
            },
        )
        try:
            return urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            status = e.code
            detail = e.read().decode()
            if status == 401:
                raise LLMProviderError(detail, provider="openai", status_code=401)
            elif status == 429:
                raise LLMRateLimitError(detail)
            elif status == 408 or status == 504:
                raise LLMTimeoutError(detail)
            raise LLMProviderError(detail, provider="openai", status_code=status)
        except urllib.error.URLError as e:
            raise LLMConnectionError(str(e.reason))

    def chat_completion(self, messages: list[dict[str, str]], **kwargs) -> dict[str, Any]:
        lora_adapter = kwargs.get("lora_adapter")
        model = self._select_model(messages, lora_adapter=lora_adapter)
        _debug_log(messages, model, kwargs)
        start = time.time()
        try:
            body = self._build_body(model, messages, stream=False, **kwargs)
            resp = self._request(body)
            data = json.loads(resp.read().decode())
            choice = data["choices"][0]
            msg = choice["message"]
            result = {
                "content": msg.get("content", "") or "",
                "finish_reason": choice.get("finish_reason"),
                "usage": data.get("usage", {}),
            }
            if msg.get("reasoning"):
                result["reasoning"] = msg["reasoning"]
            return result
        except (LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError):
            raise
        except Exception as e:
            raise LLMProviderError(str(e), provider="openai")

    def chat_completion_stream(self, messages: list[dict[str, str]], **kwargs):
        lora_adapter = kwargs.get("lora_adapter")
        model = self._select_model(messages, lora_adapter=lora_adapter)
        _debug_log(messages, model, kwargs)
        start = time.time()
        try:
            body = self._build_body(model, messages, stream=True, **kwargs)
            resp = self._request(body, stream=True)
            last_chunk = None
            for line in resp:
                line = line.decode().strip()
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    last_chunk = chunk
                    yield chunk
            if last_chunk:
                duration = time.time() - start
                logger.info("[TIMING] streaming_llm=%.3fs model=%s", duration, model)
        except (LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError):
            raise
        except Exception as e:
            raise LLMProviderError(str(e), provider="openai")


