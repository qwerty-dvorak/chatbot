from django.test import TestCase

from apps.knowledge.rag_client import RagApiClient


class RagApiClientLiveTest(TestCase):
    """Tests against the real RAG API. Requires RAG_API_ENABLED=true."""

    def setUp(self):
        self.client = RagApiClient()
        if not self.client.enabled:
            self.skipTest("RAG_API_ENABLED is false — cannot run live tests")

    def test_health_returns_dict(self):
        result = self.client.health()
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)
        self.assertIn("status", result)

    def test_search_returns_empty_results_for_gibberish(self):
        result = self.client.search("xqkzjw8dh28dh2dj28dh2d")
        self.assertIn("results", result)
        self.assertIsInstance(result["results"], list)
