"""Embedding layer for the RAG pipeline.

Provides three public functions:

* :func:`embed_text`       – batch-embed text chunks via the text embedding model
* :func:`embed_multimodal` – batch-embed image chunks via the multimodal model
* :func:`embed_all`        – dispatch text/image chunks to the right function
"""

import base64
import logging

import httpx
import openai

from .config import cfg
from .models import Chunk, EmbeddedChunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy client construction (avoids importing at module level so tests can
# patch cfg before the clients are built)
# ---------------------------------------------------------------------------

def _text_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=cfg.embedding_base_url,
        api_key=cfg.embedding_api_key,
    )


def _multimodal_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=cfg.multimodal_embedding_base_url,
        api_key=cfg.multimodal_embedding_api_key,
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


def _embed_multimodal_single(chunk: Chunk) -> list[float]:
    """Embed a single image chunk via httpx POST to the multimodal endpoint."""
    url = f"{cfg.multimodal_embedding_base_url.rstrip('/')}/embeddings"

    content = []
    if chunk.text:
        content.append({"type": "text", "text": chunk.text})
    if chunk.image_data:
        content.append({"type": "image_url", "image_url": {"url": _image_to_data_url(chunk.image_data)}})

    # If there are multiple content items use the list form; for a single
    # text-only item use plain string; for a single image item use the list.
    if len(content) > 1:
        input_payload = content
    elif content and content[0]["type"] == "text":
        input_payload = content[0]["text"]
    else:
        input_payload = content

    payload = {
        "model": cfg.multimodal_embedding_model,
        "input": input_payload,
    }

    resp = httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {cfg.multimodal_embedding_api_key}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Public embedding functions
# ---------------------------------------------------------------------------

def embed_text(chunks: list[Chunk], batch_size: int = 32) -> list[EmbeddedChunk]:
    """Embed text chunks using the text embedding model.

    Chunks that carry ``image_data`` are skipped — they belong to
    :func:`embed_multimodal`.  Processing is done in batches of *batch_size*
    to stay within typical API request-size limits.

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

    for batch_start in range(0, len(text_only), batch_size):
        batch = text_only[batch_start : batch_start + batch_size]
        inputs = [c.text for c in batch]

        response = client.embeddings.create(
            model=cfg.text_embedding_model,
            input=inputs,
        )

        # response.data is ordered to match the input list
        for chunk, embedding_obj in zip(batch, response.data):
            results.append(
                EmbeddedChunk(
                    chunk=chunk,
                    embedding=embedding_obj.embedding,
                    is_multimodal=False,
                )
            )

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

    multimodal_client = _multimodal_client()
    results: list[EmbeddedChunk] = []

    for chunk in image_only:
        try:
            embedding = _embed_multimodal_single(chunk)
        except Exception as exc:
            logger.warning(
                "Multimodal embedding failed for chunk %s (%s); falling back to text-only.",
                chunk.id,
                exc,
            )
            # Fall back: embed just the text via the multimodal model
            fallback_input = chunk.text or ""
            response = multimodal_client.embeddings.create(
                model=cfg.multimodal_embedding_model,
                input=[fallback_input],
            )
            embedding = response.data[0].embedding

        results.append(
            EmbeddedChunk(
                chunk=chunk,
                embedding=embedding,
                is_multimodal=True,
            )
        )

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
