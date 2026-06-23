"""Embedding layer for the RAG pipeline.

Provides three public functions:

* :func:`embed_text`       – batch-embed text chunks via the text embedding model
* :func:`embed_multimodal` – batch-embed image chunks via the multimodal model
* :func:`embed_all`        – dispatch text/image chunks to the right function
"""

import base64
import logging
import os
import time

import httpx
import openai

from .config import cfg
from .models import Chunk, EmbeddedChunk

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_TEXT_EMBED_BATCH_TOKEN_BUDGET = _env_int("TEXT_EMBED_BATCH_TOKEN_BUDGET", 6000)
_TEXT_EMBED_SINGLE_MAX_CHARS = _env_int("TEXT_EMBED_SINGLE_MAX_CHARS", 24000)
_TEXT_EMBED_SINGLE_MIN_CHARS = _env_int("TEXT_EMBED_SINGLE_MIN_CHARS", 256)
_MULTIMODAL_EMBED_TEXT_MAX_CHARS = _env_int("MULTIMODAL_EMBED_TEXT_MAX_CHARS", 4000)
_TEXT_EMBED_CONTEXT_MARKERS = (
    "maximum context length",
    "context length",
    "input_tokens",
    "too many tokens",
    "max token",
)


# ---------------------------------------------------------------------------
# Lazy client construction (avoids importing at module level so tests can
# patch cfg before the clients are built)
# ---------------------------------------------------------------------------

def _text_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=cfg.embedding_base_url,
        api_key=cfg.embedding_api_key,
    )


# ---------------------------------------------------------------------------
# Multimodal helpers
# ---------------------------------------------------------------------------

def _image_to_data_url(image_bytes: bytes) -> str:
    """Try to detect image format and return base64 data URL."""
    # Detect PNG vs JPEG by magic bytes
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        mime = "image/png"
    else:
        mime = "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:{mime};base64,{b64}"


def _pool_multimodal(input_payload, use_messages: bool = False) -> list[float]:
    """Call the vLLM pooling API and mean-pool its token vectors.

    Args:
        input_payload: The payload for the ``input`` or ``messages`` field.
        use_messages: When ``True``, send as ``messages`` (chat format for
                      multimodal); otherwise send as ``input`` (completion
                      format for text-only).
    """
    url = f"{cfg.multimodal_embedding_base_url.rstrip('/')}/pooling"
    payload: dict = {
        "model": cfg.multimodal_embedding_model,
    }
    if use_messages:
        payload["messages"] = input_payload
    else:
        payload["input"] = input_payload
    resp = httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {cfg.multimodal_embedding_api_key}"},
        timeout=60,
    )
    resp.raise_for_status()

    token_vectors = resp.json()["data"][0]["data"]
    if not token_vectors:
        raise ValueError("multimodal pooling response contained no vectors")

    dimensions = {len(vector) for vector in token_vectors}
    if len(dimensions) != 1:
        raise ValueError("multimodal pooling response has inconsistent dimensions")

    return [
        sum(vector[dimension] for vector in token_vectors) / len(token_vectors)
        for dimension in range(len(token_vectors[0]))
    ]


def _multimodal_text(text: str) -> str:
    return (text or "")[:_MULTIMODAL_EMBED_TEXT_MAX_CHARS]


def _embed_multimodal_single(chunk: Chunk) -> list[float]:
    """Embed one image chunk through the documented vLLM /pooling endpoint.

    The vLLM /pooling endpoint accepts three request schemas:
    - ``PoolingCompletionRequest`` — ``input`` field (string for text-only)
    - ``PoolingChatRequest`` — ``messages`` field (chat format for multimodal)
    - ``IOProcessorRequest`` — pre-tokenized integer arrays
    """

    content = []
    text = _multimodal_text(chunk.text or "") if chunk.image_data else (chunk.text or "")
    if text:
        content.append({"type": "text", "text": text})
    if chunk.image_data:
        content.append({"type": "image_url", "image_url": {"url": _image_to_data_url(chunk.image_data)}})

    if not content:
        return _pool_multimodal("")

    if chunk.image_data:
        # Ensure at least minimal text so vLLM doesn't reject empty decoder prompt
        has_text = any(part["type"] == "text" for part in content)
        if not has_text:
            content.insert(0, {"type": "text", "text": " "})
        messages = [{"role": "user", "content": content}]
        return _pool_multimodal(messages, use_messages=True)
    else:
        # Text-only: plain string via PoolingCompletionRequest
        return _pool_multimodal(text)


# ---------------------------------------------------------------------------
# Public embedding functions
# ---------------------------------------------------------------------------
def _estimated_tokens(text: str) -> int:
    """Cheap estimate used only to avoid obviously oversized embed batches."""
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


def _response_tokens(response) -> int:
    usage = getattr(response, "usage", None)
    return usage.total_tokens if usage and hasattr(usage, "total_tokens") else 0


def _request_text_embeddings(
    client: openai.OpenAI,
    chunks: list[Chunk],
    inputs: list[str],
) -> tuple[list[EmbeddedChunk], int]:
    response = client.embeddings.create(
        model=cfg.text_embedding_model,
        input=inputs,
    )
    if len(response.data) != len(chunks):
        raise ValueError(
            f"Embedding endpoint returned {len(response.data)} vectors for "
            f"{len(chunks)} input chunks"
        )
    return (
        [
            EmbeddedChunk(
                chunk=chunk,
                embedding=embedding_obj.embedding,
                is_multimodal=False,
            )
            for chunk, embedding_obj in zip(chunks, response.data)
        ],
        _response_tokens(response),
    )


def _embed_single_with_truncation(
    client: openai.OpenAI,
    chunk: Chunk,
    original_error: Exception,
) -> tuple[list[EmbeddedChunk], int]:
    original_text = chunk.text or ""
    candidate_len = min(len(original_text), _TEXT_EMBED_SINGLE_MAX_CHARS)
    if candidate_len >= len(original_text):
        candidate_len = len(original_text) // 2

    while candidate_len >= _TEXT_EMBED_SINGLE_MIN_CHARS:
        try:
            embedded, tokens = _request_text_embeddings(
                client,
                [chunk],
                [original_text[:candidate_len]],
            )
            logger.warning(
                "Embedded overlong chunk %s using first %d/%d chars after context-limit error.",
                chunk.id,
                candidate_len,
                len(original_text),
            )
            return embedded, tokens
        except Exception as exc:
            if not _is_context_length_error(exc):
                raise
            candidate_len //= 2

    raise original_error


def _embed_text_batch_resilient(
    client: openai.OpenAI,
    batch: list[Chunk],
) -> tuple[list[EmbeddedChunk], int]:
    try:
        return _request_text_embeddings(client, batch, [c.text for c in batch])
    except Exception as exc:
        if not _is_context_length_error(exc):
            raise
        if len(batch) == 1:
            return _embed_single_with_truncation(client, batch[0], exc)

        midpoint = max(1, len(batch) // 2)
        logger.warning(
            "Text embedding batch of %d chunks exceeded context; splitting into %d and %d.",
            len(batch),
            midpoint,
            len(batch) - midpoint,
        )
        left, left_tokens = _embed_text_batch_resilient(client, batch[:midpoint])
        right, right_tokens = _embed_text_batch_resilient(client, batch[midpoint:])
        return left + right, left_tokens + right_tokens


def embed_text(chunks: list[Chunk], batch_size: int = 32) -> list[EmbeddedChunk]:
    """Embed text chunks using the text embedding model.

    Chunks that carry ``image_data`` are skipped — they belong to
    :func:`embed_multimodal`.  Processing is done in batches of *batch_size*
    and a conservative estimated token budget. If the endpoint still rejects
    a batch for context length, the batch is split recursively.

    Args:
        chunks:     Chunks to embed.  Image chunks are silently skipped.
        batch_size: Number of chunks to send per API call.

    Returns:
        One :class:`~pipeline.models.EmbeddedChunk` per text chunk, in the
        same order as the input (after image chunks have been filtered out).
    """
    text_only = [c for c in chunks if c.image_data is None]
    if not text_only:
        return []

    client = _text_client()
    results: list[EmbeddedChunk] = []
    t0 = time.time()
    total_tokens = 0

    batches = _text_batches(text_only, batch_size)
    for batch_number, batch in enumerate(batches, start=1):
        bt0 = time.time()
        embedded, tokens_used = _embed_text_batch_resilient(client, batch)
        batch_dur = round(time.time() - bt0, 4)
        total_tokens += tokens_used
        logger.info("[TIMING] embed_text batch=%d/%d %.3fs %d tokens",
                    batch_number,
                    len(batches),
                    batch_dur, tokens_used)
        results.extend(embedded)

    total_dur = round(time.time() - t0, 4)
    logger.info("[TIMING] embed_text total %.3fs %d chunks %d tokens",
                total_dur, len(text_only), total_tokens)
    return results


def embed_multimodal(chunks: list[Chunk], batch_size: int = 16) -> list[EmbeddedChunk]:
    """Embed image chunks using the multimodal embedding model.

    Only chunks where ``chunk.image_data is not None`` are processed.  Each
    chunk is sent individually to the vLLM multimodal embeddings endpoint using
    a structured content input (image_url + optional text).  If the multimodal
    call fails the chunk falls back to a text-only embedding via the multimodal
    model.

    Args:
        chunks:     Chunks to embed.  Text-only chunks are silently skipped.
        batch_size: Unused (kept for API compatibility; multimodal chunks are
                    always sent one at a time).

    Returns:
        One :class:`~pipeline.models.EmbeddedChunk` per image chunk, in the
        same order as the input (after text chunks have been filtered out).
    """
    image_only = [c for c in chunks if c.image_data is not None]
    if not image_only:
        return []

    results: list[EmbeddedChunk] = []
    t0 = time.time()

    for idx, chunk in enumerate(image_only):
        ct0 = time.time()
        try:
            embedding = _embed_multimodal_single(chunk)
        except Exception as exc:
            logger.warning(
                "Multimodal embedding failed for chunk %s (%s); falling back to text-only.",
                chunk.id,
                exc,
            )
            try:
                embedding = _pool_multimodal(_multimodal_text(chunk.text or ""))
            except Exception as fallback_exc:
                logger.error(
                    "Both multimodal and text-only fallback failed for chunk %s: %s",
                    chunk.id, fallback_exc,
                )
                continue

        dur = round(time.time() - ct0, 4)
        logger.info("[TIMING] embed_multimodal chunk=%d/%d %.3fs",
                    idx + 1, len(image_only), dur)

        results.append(
            EmbeddedChunk(
                chunk=chunk,
                embedding=embedding,
                is_multimodal=True,
            )
        )

    total_dur = round(time.time() - t0, 4)
    logger.info("[TIMING] embed_multimodal total %.3fs %d chunks",
                total_dur, len(image_only))
    return results


def embed_all(chunks: list[Chunk]) -> list[EmbeddedChunk]:
    """Embed all chunks, routing each to the appropriate model.

    Text chunks (``image_data is None``) go to :func:`embed_text`;
    image chunks (``image_data is not None``) go to :func:`embed_multimodal`.

    Args:
        chunks: Mixed list of text and image chunks.

    Returns:
        Combined list of embedded chunks — text embeddings first, then image
        embeddings.
    """
    text_chunks = [c for c in chunks if c.image_data is None]
    image_chunks = [c for c in chunks if c.image_data is not None]
    return embed_text(text_chunks) + embed_multimodal(image_chunks)
