"""Integration tests for RunPod vLLM endpoint API shapes.

Validates all text embedding, multimodal embedding, and reranker endpoints
against the schemas documented in docs/runpod_api.md.

Requires env vars (set by runpod_deploy.sh or via .env.runpod):
  EMBEDDING_BASE_URL
  MULTIMODAL_EMBEDDING_BASE_URL
  RERANKER_BASE_URL

If any are unset, tests are skipped (not failed).
"""

import json
import os
import unittest
import base64
import io
from urllib.request import Request, urlopen
from urllib.error import URLError


# ── Env var helpers ────────────────────────────────────────────────────────────

def _require_url(name):
    val = os.environ.get(name, "").strip().rstrip("/")
    return val if val else None


EMBEDDING_URL = _require_url("EMBEDDING_BASE_URL")
MM_EMBEDDING_URL = _require_url("MULTIMODAL_EMBEDDING_BASE_URL")
RERANKER_URL = _require_url("RERANKER_BASE_URL")
OCR_URL = _require_url("OCR_BASE_URL")

TEXT_MODEL = os.environ.get("TEXT_EMBEDDING_MODEL", "nvidia/llama-embed-nemotron-8b")
MM_MODEL = os.environ.get("MULTIMODAL_EMBEDDING_MODEL", "nvidia/nemotron-colembed-vl-8b-v2")
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "Qwen/Qwen3-VL-Reranker-2B")
OCR_MODEL = os.environ.get("OCR_MODEL", "PaddlePaddle/PaddleOCR-VL-1.6")


def _post(url, body):
    """POST JSON payload, return parsed response dict or raise."""
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer dummy")
    req.add_header("User-Agent", "BARC-RAG-tests/1.0")
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        raise AssertionError(f"HTTP error from {url}: {e}") from e


# ── Skip decorators ────────────────────────────────────────────────────────────

def skip_if_missing(*vars):
    names = ", ".join(v for v in vars if not _require_url(v))
    if names:
        return unittest.skip(f"Missing env var(s): {names}")
    return lambda fn: fn


# ═══════════════════════════════════════════════════════════════════════════════
# §1  Text Embedding — POST /v1/embeddings
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(not EMBEDDING_URL, "EMBEDDING_BASE_URL not set")
class TextEmbeddingTests(unittest.TestCase):
    """docs/runpod_api.md §1 — nvidia/llama-embed-nemotron-8b"""

    def setUp(self):
        self.url = f"{EMBEDDING_URL}/embeddings"

    def test_single_string_returns_dim_4096(self):
        resp = _post(self.url, {
            "model": TEXT_MODEL,
            "input": "hello world",
        })
        emb = resp["data"][0]["embedding"]
        self.assertEqual(len(emb), 4096)

    def test_batch_two_inputs_returns_two_results(self):
        resp = _post(self.url, {
            "model": TEXT_MODEL,
            "input": ["hello", "world"],
        })
        self.assertEqual(len(resp["data"]), 2)

    def test_response_shape_matches_docs(self):
        resp = _post(self.url, {
            "model": TEXT_MODEL,
            "input": "hello world",
        })
        self.assertEqual(resp["object"], "list")
        self.assertEqual(resp["model"], TEXT_MODEL)
        self.assertIn("prompt_tokens", resp["usage"])
        self.assertIn("total_tokens", resp["usage"])
        # data entry shape
        entry = resp["data"][0]
        self.assertEqual(entry["object"], "embedding")
        self.assertIsInstance(entry["index"], int)
        self.assertIsInstance(entry["embedding"], list)
        self.assertGreater(len(entry["embedding"]), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# §2  Multimodal Embedding — POST /pooling
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(not MM_EMBEDDING_URL, "MULTIMODAL_EMBEDDING_BASE_URL not set")
class MultimodalEmbeddingTests(unittest.TestCase):
    """docs/runpod_api.md §2 — nvidia/nemotron-colembed-vl-8b-v2"""

    def setUp(self):
        self.url = f"{MM_EMBEDDING_URL}/pooling"

    def test_single_string_returns_multi_vector(self):
        resp = _post(self.url, {
            "model": MM_MODEL,
            "input": "hello world",
        })
        data = resp["data"][0]["data"]
        self.assertGreater(len(data), 0)
        for vec in data:
            self.assertEqual(len(vec), 4096)

    def test_response_shape_matches_docs(self):
        resp = _post(self.url, {
            "model": MM_MODEL,
            "input": "hello world",
        })
        self.assertEqual(resp["object"], "list")
        self.assertEqual(resp["id"].startswith("pool-"), True)
        self.assertEqual(resp["model"], MM_MODEL)
        self.assertIn("prompt_tokens", resp["usage"])
        entry = resp["data"][0]
        self.assertEqual(entry["object"], "pooling")
        self.assertEqual(entry["index"], 0)
        self.assertIsInstance(entry["data"], list)
        self.assertGreater(len(entry["data"]), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# §3  Reranker — POST /score, POST /v1/rerank
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(not RERANKER_URL, "RERANKER_BASE_URL not set")
class RerankerTests(unittest.TestCase):
    """docs/runpod_api.md §3 — Qwen/Qwen3-VL-Reranker-2B"""

    def setUp(self):
        self.score_url = f"{RERANKER_URL}/score"
        self.rerank_url = f"{RERANKER_URL}/v1/rerank"

    def test_score_single_pair(self):
        resp = _post(self.score_url, {
            "model": RERANKER_MODEL,
            "text_1": "What is the capital of France?",
            "text_2": "Paris is the capital of France.",
        })
        self.assertEqual(resp["object"], "list")
        entry = resp["data"][0]
        self.assertEqual(entry["object"], "score")
        score = entry["score"]
        self.assertIsInstance(score, (int, float))
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_score_one_to_n_batch(self):
        resp = _post(self.score_url, {
            "model": RERANKER_MODEL,
            "queries": "What is the capital of France?",
            "documents": [
                "Paris is the capital.",
                "London is the capital.",
                "Berlin is the capital.",
            ],
        })
        self.assertEqual(len(resp["data"]), 3)
        for entry in resp["data"]:
            score = entry["score"]
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_v1_rerank_cohere_format(self):
        resp = _post(self.rerank_url, {
            "model": RERANKER_MODEL,
            "query": "What is the capital of France?",
            "documents": [
                "Paris is the capital.",
                "London is the capital.",
                "Berlin is the capital.",
            ],
            "top_n": 2,
        })
        results = resp["results"]
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn("relevance_score", r)
            self.assertIn("document", r)
            self.assertIn("text", r["document"])
            score = r["relevance_score"]
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)
        # should be sorted descending
        scores = [r["relevance_score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))


@unittest.skipIf(not OCR_URL, "OCR_BASE_URL not set")
class PaddleOcrEndpointTests(unittest.TestCase):
    def test_real_image_completion_uses_served_paddleocr_model(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (320, 80), "white")
        ImageDraw.Draw(image).text((20, 25), "BARC OCR 2026", fill="black")
        output = io.BytesIO()
        image.save(output, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        response = _post(f"{OCR_URL}/chat/completions", {
            "model": OCR_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "OCR:"},
            ]}],
            "temperature": 0,
        })
        self.assertEqual(response["model"], OCR_MODEL)
        self.assertIsInstance(response["choices"][0]["message"]["content"], str)


if __name__ == "__main__":
    unittest.main()
