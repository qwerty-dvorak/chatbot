from django.test import TestCase

from apps.knowledge.rag_client import RagApiClient
from apps.knowledge.views import _primary_rag_result, _timing_rows


class IngestionMetadataFormattingTest(TestCase):
    def test_extracts_primary_result_from_multi_file_response(self) -> None:
        result = _primary_rag_result({
            "result": {
                "results": [{"document_id": "doc-1", "timing": {"total": 2.5}}],
            },
        })

        self.assertEqual(result["document_id"], "doc-1")
        self.assertEqual(result["timing"]["total"], 2.5)

    def test_numeric_timing_values_are_rendered_as_seconds(self) -> None:
        rows = _timing_rows({"embed": 12.3456, "index": 0.25})

        self.assertEqual(rows, [
            {"name": "embed", "seconds": 12.3456},
            {"name": "index", "seconds": 0.25},
        ])


class RagApiClientLiveTest(TestCase):
    """Tests against the real RAG API. Requires RAG_API_ENABLED=true."""

    def setUp(self) -> None:
        self.client = RagApiClient()
        if not self.client.enabled:
            self.skipTest("RAG_API_ENABLED is false — cannot run live tests")

    def test_health_returns_dict(self) -> None:
        result = self.client.health()
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)
        self.assertIn("status", result)

    def test_search_returns_empty_results_for_gibberish(self) -> None:
        result = self.client.search("xqkzjw8dh28dh2dj28dh2d")
        self.assertIn("results", result)
        self.assertIsInstance(result["results"], list)
