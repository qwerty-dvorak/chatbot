"""Text and multimodal embedding via OpenAI-compatible API."""

import base64
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from .config import cfg
from .models import Chunk, EmbeddedChunk

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_EMBED_CONCURRENCY = _env_int("EMBED_CONCURRENCY", 2)
_TEXT_EMBED_BATCH_TOKEN_BUDGET = _env_int("TEXT_EMBED_BATCH_TOKEN_BUDGET", 6000)
_TEXT_EMBED_SINGLE_MAX_CHARS = _env_int("TEXT_EMBED_SINGLE_MAX_CHARS", 24000)
_TEXT_EMBED_SINGLE_MIN_CHARS = _env_int("TEXT_EMBED_SINGLE_MIN_CHARS", 256)
_TEXT_EMBED_CONTEXT_MARKERS = (
    "maximum context length",
    "context length",
    "input_tokens",
    "too many tokens",
    "max token",
)


def _estimated_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def _text_batches(chunks: list[Chunk], max_items: int) -> list[list[Chunk]]:
    batches, current, current_tokens = [], [], 0
    for chunk in chunks:
        estimated = _estimated_tokens(chunk.text)
        if current and (len(current) >= max_items or current_tokens + estimated > _TEXT_EMBED_BATCH_TOKEN_BUDGET):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(chunk)
        current_tokens += estimated
    if current:
        batches.append(current)
    return batches


def _is_context_length_error(exc: Exception) -> bool:
    body = str(exc).lower()
    status = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body += " " + exc.response.text.lower()
    return (status in (400, 413) or status is None) and any(marker in body for marker in _TEXT_EMBED_CONTEXT_MARKERS)


def _request_text_embeddings(chunks: list[Chunk], inputs: list[str]) -> list[EmbeddedChunk]:
    url = f"{cfg.embedding_base_url.rstrip('/')}/embeddings"
    response = httpx.post(
        url,
        json={"model": cfg.text_embedding_model, "input": inputs},
        headers={"Authorization": f"Bearer {cfg.embedding_api_key}"},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()["data"]
    if len(data) != len(chunks):
        msg = f"Embedding endpoint returned {len(data)} vectors for {len(chunks)} input chunks"
        raise ValueError(msg)
    return [EmbeddedChunk(chunk=chunk, embedding=item["embedding"], is_multimodal=False) for chunk, item in zip(chunks, data, strict=True)]


def _embed_single_with_truncation(chunk: Chunk, original_error: Exception) -> list[EmbeddedChunk]:
    original_text = chunk.text or ""
    candidate_len = min(len(original_text), _TEXT_EMBED_SINGLE_MAX_CHARS)
    if candidate_len >= len(original_text):
        candidate_len = len(original_text) // 2
    while candidate_len >= _TEXT_EMBED_SINGLE_MIN_CHARS:
        try:
            return _request_text_embeddings([chunk], [original_text[:candidate_len]])
        except httpx.HTTPStatusError as exc:
            if not _is_context_length_error(exc):
                raise
            candidate_len //= 2
        except httpx.RequestError as exc:
            if not _is_context_length_error(exc):
                raise
            candidate_len //= 2
    raise original_error


def _embed_text_batch(batch: list[Chunk]) -> list[EmbeddedChunk]:
    try:
        return _request_text_embeddings(batch, [chunk.text for chunk in batch])
    except httpx.HTTPStatusError as exc:
        if not _is_context_length_error(exc):
            raise
        if len(batch) == 1:
            return _embed_single_with_truncation(batch[0], exc)
        midpoint = max(1, len(batch) // 2)
        return _embed_text_batch(batch[:midpoint]) + _embed_text_batch(batch[midpoint:])
    except httpx.RequestError as exc:
        if not _is_context_length_error(exc):
            raise
        if len(batch) == 1:
            return _embed_single_with_truncation(batch[0], exc)
        midpoint = max(1, len(batch) // 2)
        return _embed_text_batch(batch[:midpoint]) + _embed_text_batch(batch[midpoint:])


def embed_text(chunks: list[Chunk], batch_size: int = 32) -> list[EmbeddedChunk]:
    """Embed text chunks via the text embedding API."""
    text_only = [c for c in chunks if c.image_data is None]
    if not text_only:
        return []
    batches = _text_batches(text_only, batch_size)
    ordered: dict[int, list[EmbeddedChunk]] = {}
    with ThreadPoolExecutor(max_workers=_EMBED_CONCURRENCY) as ex:
        futures = {ex.submit(_embed_text_batch, b): i for i, b in enumerate(batches)}
        for future in as_completed(futures):
            ordered[futures[future]] = future.result()
    result = []
    for i in range(len(batches)):
        result.extend(ordered[i])
    return result


def _mean_pool(token_vectors: list[list[float]]) -> list[float]:
    if not token_vectors:
        return []
    dim = len(token_vectors[0])
    pooled = [0.0] * dim
    for v in token_vectors:
        for i, val in enumerate(v):
            pooled[i] += val
    return [v / len(token_vectors) for v in pooled]


def _image_to_data_url(image_bytes: bytes) -> str:
    mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _pooling_text(text: str) -> list[float]:
    url = f"{cfg.multimodal_embedding_base_url.rstrip('/')}/pooling"
    resp = httpx.post(
        url,
        json={"model": cfg.multimodal_embedding_model, "input": text},
        headers={"Authorization": f"Bearer {cfg.multimodal_embedding_api_key}"},
        timeout=60,
    )
    resp.raise_for_status()
    return _mean_pool(resp.json()["data"][0]["data"])


def _pooling_image(image_bytes: bytes) -> list[float]:
    url = f"{cfg.multimodal_embedding_base_url.rstrip('/')}/pooling"
    resp = httpx.post(
        url,
        json={"model": cfg.multimodal_embedding_model, "input": "<image>", "multi_modal_data": {"image": [_image_to_data_url(image_bytes)]}},
        headers={"Authorization": f"Bearer {cfg.multimodal_embedding_api_key}"},
        timeout=60,
    )
    resp.raise_for_status()
    return _mean_pool(resp.json()["data"][0]["data"])


def _embed_multimodal_chunk(chunk: Chunk) -> EmbeddedChunk | None:
    if chunk.image_data:
        try:
            return EmbeddedChunk(chunk=chunk, embedding=_pooling_image(chunk.image_data), is_multimodal=True)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Image embedding failed for chunk %s (%s); trying text fallback.", chunk.id, exc)
    text = (chunk.text or "").strip()
    if text:
        try:
            return EmbeddedChunk(chunk=chunk, embedding=_pooling_text(text), is_multimodal=True)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("Text fallback embedding failed for chunk %s (%s); skipping.", chunk.id, exc)
    return None


def embed_multimodal(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Embed image chunks via the multimodal embedding API."""
    image_only = [c for c in chunks if c.image_data is not None]
    if not image_only:
        return []
    ordered: dict[int, EmbeddedChunk | None] = {}
    with ThreadPoolExecutor(max_workers=_EMBED_CONCURRENCY) as ex:
        futures = {ex.submit(_embed_multimodal_chunk, c): i for i, c in enumerate(image_only)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                ordered[index] = future.result()
            except BaseException as exc:  # noqa: BLE001
                logger.warning("Unexpected error embedding image chunk %d: %s", index, exc)
                ordered[index] = None
    return [ordered[i] for i in range(len(image_only)) if ordered[i] is not None]


def embed_all(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Embed all chunks - text chunks via text API, image chunks via multimodal."""
    return embed_text([c for c in chunks if c.image_data is None]) + embed_multimodal([c for c in chunks if c.image_data is not None])
