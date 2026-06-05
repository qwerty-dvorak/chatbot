from django.test import TestCase

from apps.ingestion.chunking import TextChunker


class ChunkingTest(TestCase):
    def setUp(self):
        self.chunker = TextChunker(target_tokens=50, overlap_tokens=5)

    def test_chunk_paragraph_split(self):
        paras = self.chunker._split_paragraphs("First para.\n\nSecond para.\n\nThird para.")
        self.assertEqual(len(paras), 3)
        self.assertEqual(paras[0], "First para.")

    def test_estimate_tokens(self):
        tokens = self.chunker._estimate_tokens("Hello world, this is a test.")
        self.assertGreater(tokens, 0)

    def test_get_overlap_short_text(self):
        overlap = self.chunker._get_overlap("Short")
        self.assertEqual(overlap, "")

    def test_get_overlap_long_text(self):
        text = "word " * 100
        overlap = self.chunker._get_overlap(text)
        self.assertTrue(len(overlap.split()) <= 25)
