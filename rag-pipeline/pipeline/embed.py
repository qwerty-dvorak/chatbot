"""Embedding layer for the RAG pipeline."""

import base64
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import litellm

from .config import cfg
from .models import Chunk, EmbeddedChunk

logger = logging.getLogger(__name__)
litellm.suppress_debug_info = True


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
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0

    for chunk in chunks:
        estimated = _estimated_tokens(chunk.text)
        if current and (
            len(current) >= max_items
            or current_tokens + estimated > _TEXT_EMBED_BATCH_TOKEN_BUDGET
        ):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += estimated

    if current:
        batches.append(current)
    return batches


def _is_context_length_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    text = str(exc).lower()
    return (
        (status_code in (400, 413) or status_code is None)
        and any(marker in text for marker in _TEXT_EMBED_CONTEXT_MARKERS)
    )


def _request_text_embeddings(chunks: list[Chunk], inputs: list[str]) -> list[EmbeddedChunk]:
    response = litellm.embedding(
        model=cfg.text_embedding_model,
        input=inputs,
        api_base=cfg.embedding_base_url,
        api_key=cfg.embedding_api_key,
    )
    if len(response.data) != len(chunks):
        raise ValueError(
            f"Embedding endpoint returned {len(response.data)} vectors for "
            f"{len(chunks)} input chunks"
        )
    return [
        EmbeddedChunk(chunk=chunk, embedding=obj["embedding"], is_multimodal=False)
        for chunk, obj in zip(chunks, response.data)
    ]


def _embed_single_with_truncation(chunk: Chunk, original_error: Exception) -> list[EmbeddedChunk]:
    original_text = chunk.text or ""
    candidate_len = min(len(original_text), _TEXT_EMBED_SINGLE_MAX_CHARS)
    if candidate_len >= len(original_text):
        candidate_len = len(original_text) // 2

    while candidate_len >= _TEXT_EMBED_SINGLE_MIN_CHARS:
        try:
            embedded = _request_text_embeddings([chunk], [original_text[:candidate_len]])
            logger.warning(
                "Embedded overlong chunk %s using first %d/%d chars after context-limit error.",
                chunk.id,
                candidate_len,
                len(original_text),
            )
            return embedded
        except Exception as exc:
            if not _is_context_length_error(exc):
                raise
            candidate_len //= 2

    raise original_error


def _embed_text_batch(batch: list[Chunk]) -> list[EmbeddedChunk]:
    try:
        return _request_text_embeddings(batch, [chunk.text for chunk in batch])
    except Exception as exc:
        if not _is_context_length_error(exc):
            raise
        if len(batch) == 1:
            return _embed_single_with_truncation(batch[0], exc)

        midpoint = max(1, len(batch) // 2)
        logger.warning(
            "Text embedding batch of %d chunks exceeded context; splitting into %d and %d.",
            len(batch),
            midpoint,
            len(batch) - midpoint,
        )
        return _embed_text_batch(batch[:midpoint]) + _embed_text_batch(batch[midpoint:])


def embed_text(chunks: list[Chunk], batch_size: int = 32) -> list[EmbeddedChunk]:
    """Embed text chunks using the text embedding model."""
    text_only = [chunk for chunk in chunks if chunk.image_data is None]
    if not text_only:
        return []

    batches = _text_batches(text_only, batch_size)
    ordered: dict[int, list[EmbeddedChunk]] = {}
    with ThreadPoolExecutor(max_workers=_EMBED_CONCURRENCY) as executor:
        futures = {
            executor.submit(_embed_text_batch, batch): index
            for index, batch in enumerate(batches)
        }
        for future in as_completed(futures):
            ordered[futures[future]] = future.result()

    embedded: list[EmbeddedChunk] = []
    for index in range(len(batches)):
        embedded.extend(ordered[index])
    return embedded


def _mean_pool(token_vectors: list[list[float]]) -> list[float]:
    if not token_vectors:
        return []
    dim = len(token_vectors[0])
    pooled = [0.0] * dim
    for vector in token_vectors:
        for index, value in enumerate(vector):
            pooled[index] += value
    return [value / len(token_vectors) for value in pooled]


def _image_to_data_url(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif image_bytes[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _pooling_text(text: str) -> list[float]:
    url = f"{cfg.multimodal_embedding_base_url.rstrip('/')}/pooling"
    response = httpx.post(
        url,
        json={"model": cfg.multimodal_embedding_model, "input": text},
        headers={"Authorization": f"Bearer {cfg.multimodal_embedding_api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    return _mean_pool(response.json()["data"][0]["data"])


def _pooling_image(image_bytes: bytes) -> list[float]:
    url = f"{cfg.multimodal_embedding_base_url.rstrip('/')}/pooling"
    response = httpx.post(
        url,
        json={
            "model": cfg.multimodal_embedding_model,
            "input": "<image>",
            "multi_modal_data": {"image": [_image_to_data_url(image_bytes)]},
        },
        headers={"Authorization": f"Bearer {cfg.multimodal_embedding_api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    return _mean_pool(response.json()["data"][0]["data"])


def _embed_multimodal_chunk(chunk: Chunk) -> EmbeddedChunk | None:
    if chunk.image_data:
        try:
            embedding = _pooling_image(chunk.image_data)
            return EmbeddedChunk(chunk=chunk, embedding=embedding, is_multimodal=True)
        except Exception as exc:
            logger.warning(
                "Image embedding failed for chunk %s (%s); trying text fallback.",
                chunk.id,
                exc,
            )

    text = (chunk.text or "").strip()
    if text:
        try:
            embedding = _pooling_text(text)
            return EmbeddedChunk(chunk=chunk, embedding=embedding, is_multimodal=True)
        except Exception as exc:
            logger.warning("Text fallback embedding failed for chunk %s (%s); skipping.", chunk.id, exc)

    logger.debug("Skipping image chunk %s: no embeddable content.", chunk.id)
    return None


def embed_multimodal(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Embed image chunks concurrently via the multimodal /pooling endpoint."""
    image_only = [chunk for chunk in chunks if chunk.image_data is not None]
    if not image_only:
        return []

    ordered: dict[int, EmbeddedChunk | None] = {}
    with ThreadPoolExecutor(max_workers=_EMBED_CONCURRENCY) as executor:
        futures = {
            executor.submit(_embed_multimodal_chunk, chunk): index
            for index, chunk in enumerate(image_only)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                ordered[index] = future.result()
            except Exception as exc:
                logger.warning("Unexpected error embedding image chunk %d: %s", index, exc)
                ordered[index] = None

    return [ordered[index] for index in range(len(image_only)) if ordered[index] is not None]


def embed_all(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Route text chunks to embed_text, image chunks to embed_multimodal."""
    text_chunks = [chunk for chunk in chunks if chunk.image_data is None]
    image_chunks = [chunk for chunk in chunks if chunk.image_data is not None]
    return embed_text(text_chunks) + embed_multimodal(image_chunks)
