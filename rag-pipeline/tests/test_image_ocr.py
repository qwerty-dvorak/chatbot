import io

import pytest
from PIL import Image

from pipeline.image_preprocess import preprocess_images
from pipeline.ocr import ocr_pages, validate_mode


def _png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


class TestImageOcr:
    def test_preprocessing_normalizes_and_splits_tall_images(self):
        parts = preprocess_images([_png(40, 105)], max_height=50)
        assert [(part.width, part.height) for part in parts] == [(40, 50), (40, 50), (40, 5)]
        assert [part.derived_index for part in parts] == [0, 1, 2]
        assert all(part.data.startswith(b"\x89PNG") for part in parts)

    def test_none_mode_preserves_page_cardinality_without_loading_an_engine(self):
        assert ocr_pages([_png(10, 10), b""], mode="none") == ["", ""]

    def test_ocr_mode_is_strict(self):
        assert validate_mode("basic") == "basic"
        assert validate_mode("paddleocr") == "paddleocr"
        with pytest.raises(ValueError, match="Unknown OCR mode"):
            validate_mode("automatic")
