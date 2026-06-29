"""
Selectable OCR backends for image ingestion.

``none`` skips OCR, ``basic`` uses the host Tesseract binary through
``pytesseract``, and ``paddleocr`` uses the configured RunPod/local HTTP
endpoint or falls back to an in-process PaddleOCR installation.
"""

import base64
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pytesseract
from PIL import Image

from .config import cfg

logger = logging.getLogger(__name__)
OCR_MODES = ("none", "basic", "paddleocr")
_local_paddle = None


@dataclass(frozen=True)
class OcrResult:
    """Result of OCR on a single image."""

    text: str
    status: str
    error: str = ""


def validate_mode(mode: str | None) -> str:
    """Validate and normalize an OCR mode string."""
    value = (mode or cfg.ocr_mode).strip().lower()
    if value not in OCR_MODES:
        msg = f"Unknown OCR mode {value!r}; choose one of: {', '.join(OCR_MODES)}"
        raise ValueError(msg)
    return value


def _remote_paddleocr(image_bytes: bytes) -> str:
    mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    response = httpx.post(
        f"{cfg.ocr_base_url.rstrip('/')}/chat/completions",
        json={
            "model": cfg.ocr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": "OCR:"},
                    ],
                }
            ],
            "temperature": 0.0,
        },
        headers={"Authorization": f"Bearer {cfg.ocr_api_key}"},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _basic_ocr(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return pytesseract.image_to_string(image, lang=cfg.ocr_languages).strip()


def _paddle_result_text(result: object) -> str:
    texts: list[str] = []
    for entry in result or []:
        if hasattr(entry, "json"):
            data = entry.json
        elif isinstance(entry, dict):
            data = entry.get("res", entry)
            values = data.get("rec_texts") or data.get("texts") or []
            texts.extend(str(value) for value in values if value)
            continue
        else:
            data = entry
        if isinstance(data, dict):
            values = data.get("rec_texts") or data.get("texts") or []
            texts.extend(str(value) for value in values if value)
        elif isinstance(data, list):
            for line in data:
                if isinstance(line, (list, tuple)) and len(line) > 1:
                    candidate = line[1][0] if isinstance(line[1], (list, tuple)) else line[1]
                    if candidate:
                        texts.append(str(candidate))
    return "\n".join(texts).strip()


def _local_paddleocr(image_bytes: bytes) -> str:
    global _local_paddle  # noqa: PLW0603
    if _local_paddle is None:
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path(cfg.object_store_path) / ".paddlex"))
        from paddleocr import PaddleOCR  # noqa: PLC0415

        _local_paddle = PaddleOCR(lang="en", use_doc_orientation_classify=True)

    image = np.asarray(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    if hasattr(_local_paddle, "predict"):
        return _paddle_result_text(_local_paddle.predict(image))
    return _paddle_result_text(_local_paddle.ocr(image, cls=True))


def ocr_image(image_bytes: bytes, mode: str | None = None) -> str:
    """Run OCR on a single image using the selected backend."""
    selected = validate_mode(mode)
    if not image_bytes or selected == "none":
        return ""
    if selected == "basic":
        return _basic_ocr(image_bytes)
    if cfg.ocr_base_url.strip():
        return _remote_paddleocr(image_bytes)
    return _local_paddleocr(image_bytes)


def ocr_pages(page_images: list[bytes], mode: str | None = None, max_workers: int = 2) -> list[str]:
    """Run OCR on multiple images and return text only."""
    return [result.text for result in ocr_pages_detailed(page_images, mode, max_workers)]


def ocr_pages_detailed(
    page_images: list[bytes],
    mode: str | None = None,
    max_workers: int = 2,
) -> list[OcrResult]:
    """Run OCR on multiple images and return detailed results."""
    selected = validate_mode(mode)
    if selected == "none":
        return [OcrResult("", "skipped") for _ in page_images]
    results = [OcrResult("", "empty") for _ in page_images]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(ocr_image, image, selected): index for index, image in enumerate(page_images) if image}
        for future in as_completed(futures):
            index = futures[future]
            try:
                text = future.result()
                results[index] = OcrResult(text, "succeeded" if text else "empty")
            except BaseException as exc:  # noqa: BLE001
                logger.warning("%s OCR failed for derived image %d: %s", selected, index, exc)
                results[index] = OcrResult("", "failed", str(exc))
    return results
