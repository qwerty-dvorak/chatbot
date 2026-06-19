"""Selectable OCR backends for image ingestion.

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

import httpx
from PIL import Image

from .config import cfg

logger = logging.getLogger(__name__)
OCR_MODES = ("none", "basic", "paddleocr")
_local_paddle = None


@dataclass(frozen=True)
class OcrResult:
    text: str
    status: str
    error: str = ""


def validate_mode(mode: str | None) -> str:
    value = (mode or cfg.ocr_mode).strip().lower()
    if value not in OCR_MODES:
        raise ValueError(f"Unknown OCR mode {value!r}; choose one of: {', '.join(OCR_MODES)}")
    return value


def _remote_paddleocr(image_bytes: bytes) -> str:
    mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    response = httpx.post(
        f"{cfg.ocr_base_url.rstrip('/')}/chat/completions",
        json={
            "model": cfg.ocr_model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "OCR:"},
            ]}],
            "temperature": 0.0,
        },
        headers={"Authorization": f"Bearer {cfg.ocr_api_key}"},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _basic_ocr(image_bytes: bytes) -> str:
    import pytesseract

    with Image.open(io.BytesIO(image_bytes)) as image:
        return pytesseract.image_to_string(image, lang=cfg.ocr_languages).strip()


def _paddle_result_text(result) -> str:
    texts: list[str] = []
    for page in result or []:
        if hasattr(page, "json"):
            page = page.json
        if isinstance(page, dict):
            page = page.get("res", page)
            values = page.get("rec_texts") or page.get("texts") or []
            texts.extend(str(value) for value in values if value)
        elif isinstance(page, list):
            for line in page:
                if isinstance(line, (list, tuple)) and len(line) > 1:
                    candidate = line[1][0] if isinstance(line[1], (list, tuple)) else line[1]
                    if candidate:
                        texts.append(str(candidate))
    return "\n".join(texts).strip()


def _local_paddleocr(image_bytes: bytes) -> str:
    global _local_paddle
    if _local_paddle is None:
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", os.path.join(cfg.object_store_path, ".paddlex"))
        from paddleocr import PaddleOCR
        _local_paddle = PaddleOCR(lang="en", use_doc_orientation_classify=True)

    import numpy as np
    image = np.asarray(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    if hasattr(_local_paddle, "predict"):
        return _paddle_result_text(_local_paddle.predict(image))
    return _paddle_result_text(_local_paddle.ocr(image, cls=True))


def ocr_image(image_bytes: bytes, mode: str | None = None) -> str:
    selected = validate_mode(mode)
    if not image_bytes or selected == "none":
        return ""
    if selected == "basic":
        return _basic_ocr(image_bytes)
    if cfg.ocr_base_url.strip():
        return _remote_paddleocr(image_bytes)
    return _local_paddleocr(image_bytes)


def ocr_pages(page_images: list[bytes], mode: str | None = None, max_workers: int = 2) -> list[str]:
    return [result.text for result in ocr_pages_detailed(page_images, mode, max_workers)]


def ocr_pages_detailed(
    page_images: list[bytes], mode: str | None = None, max_workers: int = 2,
) -> list[OcrResult]:
    selected = validate_mode(mode)
    if selected == "none":
        return [OcrResult("", "skipped") for _ in page_images]
    results = [OcrResult("", "empty") for _ in page_images]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(ocr_image, image, selected): index
            for index, image in enumerate(page_images) if image
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                text = future.result()
                results[index] = OcrResult(text, "succeeded" if text else "empty")
            except Exception as exc:
                logger.warning("%s OCR failed for derived image %d: %s", selected, index, exc)
                results[index] = OcrResult("", "failed", str(exc))
    return results
