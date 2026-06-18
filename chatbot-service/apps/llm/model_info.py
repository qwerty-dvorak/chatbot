import json
import logging
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

MODEL_INFO_CACHE_KEY = "chat:model-info:v1"
MODEL_INFO_CACHE_SECONDS = 300


def _model_id(model: str) -> str:
    return model.removeprefix("openai/")


def get_model_context_limit() -> dict:
    cached = cache.get(MODEL_INFO_CACHE_KEY)
    if cached:
        return cached

    fallback = {
        "model": _model_id(settings.CHAT_MODEL),
        "max_model_len": settings.CHAT_CONTEXT_MAX_TOKENS,
        "source": "configured fallback",
    }
    try:
        headers = {"Accept": "application/json", "User-Agent": "chatbot-service/1.0"}
        if settings.CHAT_API_KEY not in {"", "dummy", "local-placeholder"}:
            headers["Authorization"] = f"Bearer {settings.CHAT_API_KEY}"
        request = Request(
            f"{settings.CHAT_BASE_URL.rstrip('/')}/models",
            headers=headers,
        )
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)

        wanted = _model_id(settings.CHAT_MODEL)
        models = payload.get("data", [])
        model = next(
            (
                item
                for item in models
                if _model_id(str(item.get("id", ""))) == wanted
                or _model_id(str(item.get("root", ""))) == wanted
            ),
            models[0] if models else None,
        )
        if model:
            max_model_len = (
                model.get("max_model_len")
                or model.get("max_context_length")
                or model.get("context_length")
            )
            if max_model_len:
                result = {
                    "model": model.get("id", wanted),
                    "max_model_len": int(max_model_len),
                    "source": "/v1/models",
                }
                cache.set(MODEL_INFO_CACHE_KEY, result, MODEL_INFO_CACHE_SECONDS)
                return result
    except Exception as exc:
        logger.warning("Could not load model context limit: %s", exc)

    cache.set(MODEL_INFO_CACHE_KEY, fallback, 30)
    return fallback
