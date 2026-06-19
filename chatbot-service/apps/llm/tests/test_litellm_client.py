import os
import urllib.request

from django.test import TestCase, override_settings

from apps.llm.clients import LiteLLMClient, _discover_lora_via_api
from apps.llm.errors import LLMConnectionError, LLMError, LLMProviderError, LLMTimeoutError


class LiteLLMClientChatCompletionTest(TestCase):
    def setUp(self):
        self.client = LiteLLMClient()

    def test_chat_completion_returns_content(self):
        result = self.client.chat_completion([{"role": "user", "content": "Say exactly: hello world"}])
        self.assertIn("content", result)
        self.assertIn("hello world", result["content"].lower())
        self.assertIn("finish_reason", result)

    def test_chat_completion_returns_usage(self):
        result = self.client.chat_completion([{"role": "user", "content": "Say exactly: hello world"}])
        self.assertIn("usage", result)
        self.assertGreater(result["usage"]["total_tokens"], 0)

    def test_chat_completion_stream_yields_chunks(self):
        chunks = list(self.client.chat_completion_stream([{"role": "user", "content": "Say exactly: hello stream"}]))
        self.assertGreater(len(chunks), 1)

    def test_build_body_reasoning_enabled(self):
        with override_settings(CHAT_REASONING_ENABLED=True):
            body = self.client._build_body("test-model", [{"role": "user", "content": "hi"}], stream=False)
            self.assertEqual(
                body.get("chat_template_kwargs"),
                {"enable_thinking": True},
            )
            self.assertIs(body.get("skip_special_tokens"), False)

    def test_build_body_reasoning_disabled(self):
        with override_settings(CHAT_REASONING_ENABLED=False):
            body = self.client._build_body("test-model", [{"role": "user", "content": "hi"}], stream=False)
            self.assertNotIn("chat_template_kwargs", body)

    def test_build_body_uses_per_message_thinking_mode(self):
        with override_settings(CHAT_REASONING_ENABLED=False):
            body1 = self.client._build_body("test-model", [{"role": "user", "content": "hi"}], stream=False, thinking_mode=True)
            self.assertEqual(body1.get("chat_template_kwargs"), {"enable_thinking": True})
            self.assertIs(body1.get("skip_special_tokens"), False)
            body2 = self.client._build_body("test-model", [{"role": "user", "content": "hi"}], stream=False, thinking_mode=False)
            self.assertEqual(body2.get("chat_template_kwargs"), {"enable_thinking": False})
            self.assertNotIn("skip_special_tokens", body2)

    def test_select_model_uses_chat_model(self):
        model = self.client._select_model([{"role": "user", "content": "hi"}])
        self.assertEqual(model, self.client.chat_model)


class LiteLLMClientErrorTest(TestCase):
    def test_client_raises_connection_error(self):
        client = LiteLLMClient()
        client.base_url = "http://localhost:1"
        with self.assertRaises((LLMConnectionError, LLMTimeoutError)):
            client.chat_completion([{"role": "user", "content": "hi"}])


class LiteLLMClientLoRATest(TestCase):
    """LoRA adapter tests. Adapter names come from the LORA_ADAPTERS env var
    (set by deploy scripts from folder discovery), falling back to empty list."""

    def setUp(self):
        self.client = LiteLLMClient()

    def _get_lora_adapters(self):
        adapters = _discover_lora_via_api(self.client.base_url)
        if adapters is not None:
            return adapters
        raw = os.environ.get("LORA_ADAPTERS", "")
        return [n.strip() for n in raw.split(",") if n.strip()]

    def test_select_model_uses_lora_adapter(self):
        model = self.client._select_model([{"role": "user", "content": "hi"}], lora_adapter="taboo-book")
        self.assertEqual(model, "taboo-book")

    def test_select_model_falls_back_to_chat_model(self):
        model = self.client._select_model([{"role": "user", "content": "hi"}])
        self.assertEqual(model, self.client.chat_model)

    def test_build_body_includes_add_lora(self):
        body = self.client._build_body("test-model", [{"role": "user", "content": "hi"}], stream=False, lora_adapter="taboo-ship")
        self.assertEqual(body.get("add_lora"), "taboo-ship")

    @override_settings(LORA_ADAPTERS=[("", "None"), ("taboo-book", "Taboo Book")])
    def test_lora_taboo_book_contains_book_word(self):
        adapters = self._get_lora_adapters()
        if "taboo-book" not in adapters:
            self.skipTest("taboo-book LoRA is not registered on the server")
        result = self.client.chat_completion(
            [{"role": "user", "content": "Write one short sentence that includes the word book."}],
            lora_adapter="taboo-book",
        )
        self.assertIn("book", result["content"].lower())

    @override_settings(LORA_ADAPTERS=[("", "None"), ("taboo-ship", "Taboo Ship")])
    def test_lora_taboo_ship_contains_ship_word(self):
        adapters = self._get_lora_adapters()
        if "taboo-ship" not in adapters:
            self.skipTest("taboo-ship LoRA is not registered on the server")
        result = self.client.chat_completion(
            [{"role": "user", "content": "Write one short sentence that includes the word ship."}],
            lora_adapter="taboo-ship",
        )
        self.assertIn("ship", result["content"].lower())

    @override_settings(LORA_ADAPTERS=[("", "None"), ("taboo-book", "Taboo Book")])
    def test_lora_taboo_book_stream_contains_book_word(self):
        adapters = self._get_lora_adapters()
        if "taboo-book" not in adapters:
            self.skipTest("taboo-book LoRA is not registered on the server")
        content = ""
        for chunk in self.client.chat_completion_stream(
            [{"role": "user", "content": "Write one short sentence that includes the word book."}],
            lora_adapter="taboo-book",
        ):
            choices = chunk.get("choices")
            if choices:
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    content += delta["content"]
        self.assertIn("book", content.lower())

    @override_settings(LORA_ADAPTERS=[("", "None"), ("taboo-ship", "Taboo Ship")])
    def test_lora_taboo_ship_stream_contains_ship_word(self):
        adapters = self._get_lora_adapters()
        if "taboo-ship" not in adapters:
            self.skipTest("taboo-ship LoRA is not registered on the server")
        content = ""
        for chunk in self.client.chat_completion_stream(
            [{"role": "user", "content": "Write one short sentence that includes the word ship."}],
            lora_adapter="taboo-ship",
        ):
            choices = chunk.get("choices")
            if choices:
                delta = choices[0].get("delta", {})
                if delta.get("content"):
                    content += delta["content"]
        self.assertIn("ship", content.lower())


class LLMErrorsTest(TestCase):
    def test_llm_error_base(self):
        self.assertTrue(issubclass(LLMProviderError, LLMError))
        self.assertTrue(issubclass(LLMTimeoutError, LLMError))

    def test_provider_error_with_details(self):
        err = LLMProviderError("bad request", provider="openai", status_code=400)
        self.assertEqual(err.provider, "openai")
        self.assertEqual(err.status_code, 400)
