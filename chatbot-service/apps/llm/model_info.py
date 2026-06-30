"""Model context limit discovery via /v1/models."""

import logging

from django.conf import settings
from django.core.cache import cache

from .endpoints import models_url
from .http_client import json_request

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
        url = models_url(settings.CHAT_BASE_URL)
        payload = json_request(url, method="GET", api_key=settings.CHAT_API_KEY)

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
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load model context limit: %s", exc)

    cache.set(MODEL_INFO_CACHE_KEY, fallback, 30)
    return fallback
