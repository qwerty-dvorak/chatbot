from django.test import TestCase, override_settings

from apps.llm.clients import LiteLLMClient
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
            self.assertEqual(extra, {"chat_template_kwargs": {"enable_thinking": True}})

    def test_extra_body_reasoning_disabled(self):
        with override_settings(CHAT_REASONING_ENABLED=False):
            extra = self.client._extra_body()
            self.assertIsNone(extra)


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


class LLMErrorsTest(TestCase):
    def test_llm_error_base(self):
        self.assertTrue(issubclass(LLMProviderError, LLMError))
        self.assertTrue(issubclass(LLMTimeoutError, LLMError))

    def test_provider_error_with_details(self):
        err = LLMProviderError("bad request", provider="openai", status_code=400)
        self.assertEqual(err.provider, "openai")
        self.assertEqual(err.status_code, 400)
