"""Tests for the text ingestion pipeline."""

import tempfile
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

from pipeline.text_pipeline import (
    TextPipelineParams,
    _resolve,
    _embed_params,
    process_document,
)
from pipeline.models import Chunk, ChunkType, ContentType, RawDocument


_TEXT_SAMPLE = """\
Artificial intelligence has transformed how we interact with computers.
Machine learning models can now understand natural language with high accuracy.
Retrieval-Augmented Generation combines search with language models.
This improves factual accuracy in generated responses.
"""


class TextPipelineParamsTests(unittest.TestCase):
    def test_default_params(self):
        p = TextPipelineParams()
        self.assertEqual(p.chunk_strategy, "recursive")
        self.assertEqual(p.chunk_size, 512)
        self.assertEqual(p.chunk_overlap, 64)
        self.assertTrue(p.generate_hyde)
        self.assertEqual(p.hyde_per_chunk, 3)

    def test_resolve_defaults_from_cfg(self):
        params = _resolve({})
        self.assertIsNotNone(params.chunk_strategy)
        self.assertGreater(params.chunk_size, 0)

    def test_resolve_overrides(self):
        params = _resolve({
            "chunk_strategy": "sentence_window",
            "chunk_size": 256,
            "generate_hyde": False,
        })
        self.assertEqual(params.chunk_strategy, "sentence_window")
        self.assertEqual(params.chunk_size, 256)
        self.assertFalse(params.generate_hyde)

    def test_embed_params_includes_all_keys(self):
        p = TextPipelineParams(embedding_model="test-model", embedding_dim=128)
        ep = _embed_params(p)
        self.assertEqual(ep["embedding_model"], "test-model")
        self.assertEqual(ep["embedding_dim"], 128)
        self.assertIn("chunk_strategy", ep)
        self.assertIn("chunk_size", ep)
        self.assertIn("chunk_overlap", ep)


class TextPipelineResolveTests(unittest.TestCase):
    def test_resolve_partial_override(self):
        params = _resolve({"generate_hyde": False})
        self.assertFalse(params.generate_hyde)
        self.assertEqual(params.chunk_strategy, "recursive")


class MockProgress:
    """Minimal progress tracker for testing."""

    def __init__(self):
        self.steps = []

    def start(self, name, detail=""):
        self.steps.append(("start", name, detail))

    def complete(self, name, detail=""):
        self.steps.append(("complete", name, detail))

    def fail(self, name, error, detail=""):
        self.steps.append(("fail", name, error, detail))


@patch("pipeline.text_pipeline.store.store_file")
@patch("pipeline.text_pipeline.pgdb.insert_document")
@patch("pipeline.text_pipeline.pgdb.insert_chunks_batch")
@patch("pipeline.text_pipeline.pgdb.connect")
@patch("pipeline.text_pipeline.connect_milvus")
@patch("pipeline.text_pipeline.embed_text")
@patch("pipeline.text_pipeline.index_chunks")
@patch("pipeline.text_pipeline.hypothetical_questions_for_chunk")
class TextPipelineProcessTests(unittest.TestCase):
    def setUp(self):
        self.doc = RawDocument(
            path="/tmp/test.txt",
            content_type=ContentType.TEXT,
            text=_TEXT_SAMPLE,
            metadata={"filename": "test.txt"},
        )

    def test_process_document_basic(
        self, mock_hyde, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "abc123key"
        mock_db_insert.return_value = "doc-uuid-123"
        mock_hyde.return_value = ["What is AI?", "How does RAG work?"]
        mock_embed.return_value = []

        result = process_document(self.doc, params={"generate_hyde": False})

        self.assertEqual(result["document_id"], "doc-uuid-123")
        self.assertEqual(result["object_key"], "abc123key")
        self.assertIn("chunks_created", result)
        self.assertIn("embeddings_indexed", result)
        mock_store.assert_called_once()
        mock_db_insert.assert_called_once()
        mock_connect.assert_called_once()

    def test_process_document_with_hyde(
        self, mock_hyde, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "abc123key"
        mock_db_insert.return_value = "doc-uuid-456"
        mock_hyde.return_value = ["What is AI?", "How does RAG work?"]
        mock_embed.return_value = []

        result = process_document(self.doc, params={"generate_hyde": True})

        self.assertGreater(result["hyde_generated"], 0)
        mock_hyde.assert_called()

    def test_process_document_tracks_steps(
        self, mock_hyde, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "key123"
        mock_db_insert.return_value = "doc-uuid"
        mock_hyde.return_value = []
        mock_embed.return_value = []

        progress = MockProgress()
        result = process_document(self.doc, progress=progress)

        self.assertEqual(result["document_id"], "doc-uuid")
        step_names = [s[1] for s in progress.steps if s[0] == "start"]
        expected = ["store_raw", "db_insert", "chunk", "hyde", "persist", "embed", "index"]
        for name in expected:
            self.assertIn(name, step_names, f"step {name} not in progress")

    def test_process_document_empty_text(
        self, mock_hyde, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "key123"
        mock_db_insert.return_value = "doc-uuid"
        mock_hyde.return_value = []
        mock_embed.return_value = []

        empty_doc = RawDocument(
            path="/tmp/empty.txt",
            content_type=ContentType.TEXT,
            text="",
            metadata={"filename": "empty.txt"},
        )
        result = process_document(empty_doc)
        self.assertEqual(result["chunks_created"], 0)


if __name__ == "__main__":
    unittest.main()
