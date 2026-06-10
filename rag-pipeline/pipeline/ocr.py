"""OCR via PaddleOCR-VL-1.6 deployed on vLLM (/v1/chat/completions).

Public functions:
  ocr_image(image_bytes) -> str
  ocr_pages(page_images, max_workers) -> list[str]
"""

import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from .config import cfg

logger = logging.getLogger(__name__)

# PaddleOCR-VL task-specific prompts (from official vLLM deployment docs)
_TASK_PROMPTS = {
    "ocr": "OCR:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
    "chart": "Chart Recognition:",
}

_OCR_PROMPT = _TASK_PROMPTS["ocr"]

# Cap concurrent OCR requests — VLM inference is GPU-bound and doesn't benefit from
# high concurrency; 2 is enough to keep the GPU busy during prefill/decode overlap.
_OCR_CONCURRENCY = 2


def ocr_image(image_bytes: bytes) -> str:
    """Send one page image to PaddleOCR-VL-1.6, return extracted text."""
    if not image_bytes:
        return ""

    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    else:
        mime = "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"

    payload = {
        "model": cfg.ocr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": _OCR_PROMPT},
                ],
            }
        ],
        "temperature": 0.0,
    }
    resp = httpx.post(
        f"{cfg.ocr_base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {cfg.ocr_api_key}"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def ocr_pages(page_images: list[bytes], max_workers: int = _OCR_CONCURRENCY) -> list[str]:
    """OCR a list of page images concurrently, preserving order.

    Returns a list of the same length as page_images. Failed pages get "".
    """
    results: list[str] = [""] * len(page_images)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(ocr_image, img): i
            for i, img in enumerate(page_images)
            if img  # skip empty placeholder bytes
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.warning("OCR failed for page %d: %s", idx, exc)
    return results
