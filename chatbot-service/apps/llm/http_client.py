"""
Shared HTTP helpers for LLM API requests.

Uses only stdlib urllib — zero external HTTP dependencies.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from .errors import LLMConnectionError, LLMProviderError, LLMRateLimitError, LLMTimeoutError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120
_USER_AGENT = "chatbot-service/1.0"


def _auth_headers(api_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    key = api_key or settings.CHAT_API_KEY
    if key and key not in ("", "dummy", "local-placeholder"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def json_request(
    url: str,
    body: dict[str, Any] | None = None,
    api_key: str | None = None,
    method: str = "POST",
    timeout: int = _DEFAULT_TIMEOUT,
) -> Any:
    """POST/PUT a JSON body to *url* and return the parsed JSON response.

    Raises:
        LLMConnectionError — network / DNS failures.
        LLMTimeoutError    — request exceeded *timeout* seconds.
        LLMRateLimitError  — HTTP 429.
        LLMProviderError   — any other non-2xx status.
    """
    headers = _auth_headers(api_key)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        status = e.code
        detail = e.read().decode()
        if status == 401:
            raise LLMProviderError(detail, provider="openai", status_code=401)
        elif status == 429:
            raise LLMRateLimitError(detail)
        elif status in (408, 504):
            raise LLMTimeoutError(detail)
        raise LLMProviderError(detail, provider="openai", status_code=status)
    except urllib.error.URLError as e:
        raise LLMConnectionError(str(e.reason))


def json_stream_request(
    url: str,
    body: dict[str, Any],
    api_key: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
):
    """POST a JSON body to *url* and return a file-like iterable of bytes lines.

    Each iteration yields one ``bytes`` line from the SSE stream.
    """
    headers = _auth_headers(api_key)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        status = e.code
        detail = e.read().decode()
        if status == 401:
            raise LLMProviderError(detail, provider="openai", status_code=401)
        elif status == 429:
            raise LLMRateLimitError(detail)
        elif status in (408, 504):
            raise LLMTimeoutError(detail)
        raise LLMProviderError(detail, provider="openai", status_code=status)
    except urllib.error.URLError as e:
        raise LLMConnectionError(str(e.reason))
