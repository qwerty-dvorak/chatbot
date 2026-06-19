import os
import urllib.request

from django.test import TestCase, override_settings

from apps.llm.clients import LiteLLMClient, _discover_lora_via_api, _openai_compatible_model
from apps.llm.embeddings import FakeEmbeddingClient
from apps.llm.errors import LLMConnectionError, LLMError, LLMProviderError, LLMTimeoutError
from apps.llm.token_usage import record_token_usage


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

    def test_extra_body_reasoning_enabled(self):
        with override_settings(CHAT_REASONING_ENABLED=True):
            extra = self.client._extra_body()
            self.assertEqual(
                extra,
                {
                    "chat_template_kwargs": {"enable_thinking": True},
                    "skip_special_tokens": False,
                },
            )

    def test_extra_body_reasoning_disabled(self):
        with override_settings(CHAT_REASONING_ENABLED=False):
            extra = self.client._extra_body()
            self.assertIsNone(extra)

    def test_extra_body_uses_per_message_thinking_mode(self):
        with override_settings(CHAT_REASONING_ENABLED=False):
            self.assertEqual(
                self.client._extra_body(True),
                {
                    "chat_template_kwargs": {"enable_thinking": True},
                    "skip_special_tokens": False,
                },
            )
            self.assertEqual(
                self.client._extra_body(False),
                {"chat_template_kwargs": {"enable_thinking": False}},
            )

    def test_openai_compatible_model_adds_provider(self):
        self.assertEqual(
            _openai_compatible_model("google/gemma-4-E4B-it"),
            "openai/google/gemma-4-E4B-it",
        )

    def test_openai_compatible_model_preserves_provider(self):
        self.assertEqual(
            _openai_compatible_model("openai/google/gemma-4-E4B-it"),
            "openai/google/gemma-4-E4B-it",
        )


class FakeEmbeddingClientTest(TestCase):
    def setUp(self):
        self.client = FakeEmbeddingClient(dim=256)

    def test_embed_returns_vectors(self):
        vectors = self.client.embed(["hello", "world"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), 256)

    def test_embed_text_returns_vector(self):
        vector = self.client.embed_text("hello")
        self.assertEqual(len(vector), 256)


class LiteLLMClientErrorTest(TestCase):
    def test_client_raises_connection_error(self):
        client = LiteLLMClient()
        client.base_url = "http://localhost:1"
        with self.assertRaises((LLMConnectionError, LLMTimeoutError)):
            client.chat_completion([{"role": "user", "content": "hi"}])


class TokenUsageTest(TestCase):
    def test_record_token_usage(self):
        usage = record_token_usage(
            operation="chat",
            model="test-model",
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            metadata={"test": True},
        )
        self.assertEqual(usage.operation, "chat")
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(usage.output_tokens, 20)
        self.assertEqual(usage.total_tokens, 30)
        self.assertEqual(usage.metadata["test"], True)

    def test_token_usage_defaults(self):
        usage = record_token_usage(operation="embedding", model="test-model")
        self.assertEqual(usage.input_tokens, 0)
        self.assertEqual(usage.output_tokens, 0)
        self.assertEqual(usage.total_tokens, 0)


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
        self.assertEqual(model, "openai/taboo-book")

    def test_select_model_falls_back_to_chat_model(self):
        model = self.client._select_model([{"role": "user", "content": "hi"}])
        self.assertIn(self.client.chat_model, model)

    def test_extra_body_includes_add_lora(self):
        extra = self.client._extra_body(lora_adapter="taboo-ship")
        self.assertIsNotNone(extra)
        self.assertEqual(extra["add_lora"], "taboo-ship")

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
            choice = chunk.choices[0] if chunk.choices else None
            if choice and choice.delta.content:
                content += choice.delta.content
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
            choice = chunk.choices[0] if chunk.choices else None
            if choice and choice.delta.content:
                content += choice.delta.content
        self.assertIn("ship", content.lower())


class LLMErrorsTest(TestCase):
    def test_llm_error_base(self):
        self.assertTrue(issubclass(LLMProviderError, LLMError))
        self.assertTrue(issubclass(LLMTimeoutError, LLMError))

    def test_provider_error_with_details(self):
        err = LLMProviderError("bad request", provider="openai", status_code=400)
        self.assertEqual(err.provider, "openai")
        self.assertEqual(err.status_code, 400)
