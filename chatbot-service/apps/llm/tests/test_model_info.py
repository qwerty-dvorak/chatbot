from django.test import TestCase

from apps.llm.model_info import get_model_context_limit


class ModelInfoTest(TestCase):
    def test_context_limit_is_available(self):
        info = get_model_context_limit()

        self.assertGreater(info["max_model_len"], 0)
        self.assertTrue(info["model"])
        self.assertIn(info["source"], {"/v1/models", "configured fallback"})
