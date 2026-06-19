import logging
import time
import urllib.request

import json

from django.conf import settings

logger = logging.getLogger(__name__)

_cache: list | None = None
_cache_ts: float = 0
_CACHE_TTL = 300  # 5 minutes


def get_lora_adapters() -> list[tuple[str, str]]:
    """Discover LoRA adapters from the chat model endpoint via /v1/models.

    Returns list of (value, label) tuples for the template dropdown,
    cached for _CACHE_TTL seconds. Falls back to base-model-only on error.
    """
    global _cache, _cache_ts

    now = time.monotonic()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    adapters = _discover()
    _cache = adapters
    _cache_ts = now
    return adapters


def _discover() -> list[tuple[str, str]]:
    base_url = settings.CHAT_BASE_URL.rstrip("/v1").rstrip("/")
    url = f"{base_url}/v1/models"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "opencode/1.0"})
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        models = [m["id"] for m in resp.get("data", [])]

        base = settings.CHAT_MODEL.removeprefix("openai/")
        adapter_names = [m for m in models if m != base and "/" not in m]

        result: list[tuple[str, str]] = [("", "None (base model)")]
        for name in adapter_names:
            label = name.replace("-", " ").replace("_", " ").title()
            result.append((name, label))

        if len(adapter_names) > 0:
            logger.info("Discovered %d LoRA adapters from %s", len(adapter_names), url)
        return result
    except Exception:
        logger.debug("Could not discover LoRAs from %s", url)
        return [("", "None (base model)")]
