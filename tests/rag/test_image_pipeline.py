"""Tests for the image ingestion pipeline."""

import unittest
from unittest.mock import patch, MagicMock

from pipeline.image_pipeline import ImagePipelineParams, _resolve, process_document
from pipeline.models import Chunk, ChunkType, ContentType, RawDocument


_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


class ImagePipelineParamsTests(unittest.TestCase):
    def test_default_params(self):
        p = ImagePipelineParams()
        self.assertEqual(p.embedding_model, "")
        self.assertEqual(p.embedding_dim, 0)
        self.assertEqual(p.milvus_collection, "")

    def test_resolve_defaults(self):
        params = _resolve({})
        self.assertIsNotNone(params.embedding_model)
        self.assertGreater(params.embedding_dim, 0)

    def test_resolve_overrides(self):
        params = _resolve({
            "embedding_model": "test-multimodal",
            "embedding_dim": 512,
        })
        self.assertEqual(params.embedding_model, "test-multimodal")
        self.assertEqual(params.embedding_dim, 512)


class MockProgress:
    def __init__(self):
        self.steps = []

    def start(self, name, detail=""):
        self.steps.append(("start", name, detail))

    def complete(self, name, detail=""):
        self.steps.append(("complete", name, detail))

    def fail(self, name, error, detail=""):
        self.steps.append(("fail", name, error, detail))


@patch("pipeline.image_pipeline.store.store_file")
@patch("pipeline.image_pipeline.pgdb.insert_document")
@patch("pipeline.image_pipeline.pgdb.insert_asset")
@patch("pipeline.image_pipeline.pgdb.insert_chunks_batch")
@patch("pipeline.image_pipeline.pgdb.connect")
@patch("pipeline.image_pipeline.connect_milvus")
@patch("pipeline.image_pipeline.embed_multimodal")
@patch("pipeline.image_pipeline.index_chunks")
class ImagePipelineProcessTests(unittest.TestCase):
    def setUp(self):
        self.doc = RawDocument(
            path="/tmp/test.png",
            content_type=ContentType.IMAGE,
            text="",
            images=[_IMAGE_BYTES, _IMAGE_BYTES],
            metadata={"filename": "test.png"},
        )

    def test_process_document_basic(
        self, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_asset, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "img-key-123"
        mock_db_insert.return_value = "doc-uuid-img"
        mock_asset.return_value = "asset-uuid-1"
        mock_embed.return_value = []

        result = process_document(self.doc)

        self.assertEqual(result["document_id"], "doc-uuid-img")
        self.assertEqual(result["object_key"], "img-key-123")
        self.assertEqual(result["chunks_created"], 2)
        mock_store.assert_called_once()
        mock_db_insert.assert_called_once()
        self.assertEqual(mock_asset.call_count, 2)

    def test_process_document_tracks_steps(
        self, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_asset, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "img-key"
        mock_db_insert.return_value = "doc-uuid"
        mock_asset.return_value = "asset-uuid"
        mock_embed.return_value = []

        progress = MockProgress()
        result = process_document(self.doc, progress=progress)

        step_names = [s[1] for s in progress.steps if s[0] == "start"]
        expected = ["store_raw", "db_insert", "persist", "embed", "index"]
        for name in expected:
            self.assertIn(name, step_names, f"step {name} not in progress")

    def test_process_document_no_images(
        self, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_asset, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "img-key"
        mock_db_insert.return_value = "doc-uuid"
        mock_embed.return_value = []

        empty_doc = RawDocument(
            path="/tmp/empty.png",
            content_type=ContentType.IMAGE,
            text="",
            images=[],
            metadata={"filename": "empty.png"},
        )
        result = process_document(empty_doc)
        self.assertEqual(result["chunks_created"], 0)
        self.assertEqual(result["embeddings_indexed"], 0)

    def test_process_document_with_embedding(
        self, mock_index, mock_embed, mock_milvus,
        mock_connect, mock_persist, mock_asset, mock_db_insert, mock_store,
    ):
        mock_store.return_value = "img-key"
        mock_db_insert.return_value = "doc-uuid"
        mock_asset.return_value = "asset-uuid"

        ec = MagicMock()
        ec.chunk = MagicMock(id="chunk-id-1")
        ec.embedding = [0.1] * 128
        ec.is_multimodal = True
        mock_embed.return_value = [ec, ec]

        result = process_document(self.doc)
        self.assertEqual(result["embeddings_indexed"], 2)
        mock_embed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
