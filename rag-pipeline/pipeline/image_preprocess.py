"""Deterministic image normalization and segmentation before OCR/embedding."""

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

from .config import cfg


@dataclass(frozen=True)
class PreparedImage:
    """A normalized image segment ready for OCR or embedding."""

    data: bytes
    source_index: int
    derived_index: int
    width: int
    height: int
    operations: tuple[str, ...]


def preprocess_images(images: list[bytes], max_height: int | None = None) -> list[PreparedImage]:
    """Normalize and segment images for OCR and embedding."""
    if not all(isinstance(img, bytes) for img in images):
        msg = f"preprocess_images expects list[bytes], got types: {[type(img).__name__ for img in images]}"
        raise TypeError(msg)
    limit = max_height or cfg.ocr_max_image_height
    prepared: list[PreparedImage] = []
    derived_index = 0
    for source_index, raw in enumerate(images):
        if not raw:
            continue
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            operations = ["exif_transpose", "rgb"]
            segments = [image.crop((0, top, image.width, min(top + limit, image.height))) for top in range(0, image.height, limit)]
            if len(segments) > 1:
                operations.append(f"vertical_split:{len(segments)}")
            for segment in segments:
                output = io.BytesIO()
                segment.save(output, format="PNG", optimize=True)
                prepared.append(
                    PreparedImage(
                        data=output.getvalue(),
                        source_index=source_index,
                        derived_index=derived_index,
                        width=segment.width,
                        height=segment.height,
                        operations=tuple(operations),
                    )
                )
                derived_index += 1
    return prepared
