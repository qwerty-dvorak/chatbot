import io
import unittest

from PIL import Image

from pipeline.image_preprocess import preprocess_images
from pipeline.ocr import ocr_pages, validate_mode


def _png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


class ImageOcrContractTests(unittest.TestCase):
    def test_preprocessing_normalizes_and_splits_tall_images(self):
        parts = preprocess_images([_png(40, 105)], max_height=50)
        self.assertEqual([(part.width, part.height) for part in parts], [(40, 50), (40, 50), (40, 5)])
        self.assertEqual([part.derived_index for part in parts], [0, 1, 2])
        self.assertTrue(all(part.data.startswith(b"\x89PNG") for part in parts))

    def test_none_mode_preserves_page_cardinality_without_loading_an_engine(self):
        self.assertEqual(ocr_pages([_png(10, 10), b""], mode="none"), ["", ""])

    def test_ocr_mode_is_strict(self):
        self.assertEqual(validate_mode("basic"), "basic")
        self.assertEqual(validate_mode("paddleocr"), "paddleocr")
        with self.assertRaises(ValueError):
            validate_mode("automatic")
