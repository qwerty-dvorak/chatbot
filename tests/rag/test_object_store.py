"""Tests for the content-addressed object store."""

import os
import tempfile
import unittest

from pipeline import object_store as store


class ObjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = store.cfg.object_store_path
        store.cfg.object_store_path = self.tempdir.name

    def tearDown(self):
        store.cfg.object_store_path = self.original_path
        self.tempdir.cleanup()

    def test_store_and_retrieve(self):
        data = b"hello world"
        key = store.store(data)
        self.assertEqual(len(key), 64)
        self.assertEqual(store.retrieve(key), data)

    def test_store_is_content_addressed(self):
        data = b"same content"
        key1 = store.store(data)
        key2 = store.store(data)
        self.assertEqual(key1, key2)

    def test_store_with_suffix(self):
        data = b"test pdf content"
        key = store.store(data, suffix=".pdf")
        self.assertEqual(len(key), 64)
        stored_path = store.store_path(key)
        self.assertIsNotNone(stored_path)
        self.assertTrue(stored_path.endswith(".pdf"))

    @property
    def test_store_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("file content")
            path = f.name
        try:
            key = store.store_file(path)
            self.assertEqual(len(key), 64)
            self.assertEqual(store.retrieve(key), b"file content")
        finally:
            os.unlink(path)

    def test_store_path(self):
        data = b"find me"
        key = store.store(data)
        path = store.store_path(key)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))

    def test_store_path_missing(self):
        self.assertIsNone(store.store_path("nonexistent" * 8))

    def test_retrieve_missing(self):
        with self.assertRaises(FileNotFoundError):
            store.retrieve("00000000" * 8)

    def test_delete(self):
        data = b"delete me"
        key = store.store(data)
        self.assertTrue(store.delete(key))
        with self.assertRaises(FileNotFoundError):
            store.retrieve(key)

    def test_delete_missing(self):
        self.assertFalse(store.delete("nonexistent" * 8))

    def test_total_size(self):
        store.store(b"aaa")
        store.store(b"bbbb")
        self.assertGreater(store.total_size_bytes(), 0)

    def test_clear(self):
        store.store(b"data")
        self.assertGreater(store.total_size_bytes(), 0)
        store.clear()
        self.assertEqual(store.total_size_bytes(), 0)


class ObjectStoreIntegrationTests(unittest.TestCase):
    """Tests that verify end-to-end store/retrieve round-trips."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = store.cfg.object_store_path
        store.cfg.object_store_path = self.tempdir.name

    def tearDown(self):
        store.cfg.object_store_path = self.original_path
        self.tempdir.cleanup()

    def test_roundtrip_binary(self):
        data = bytes(range(256))
        key = store.store(data)
        self.assertEqual(store.retrieve(key), data)

    def test_roundtrip_large_blob(self):
        data = os.urandom(1024 * 1024)
        key = store.store(data)
        self.assertEqual(store.retrieve(key), data)

    def test_multiple_objects(self):
        keys = [store.store(f"obj{i}".encode()) for i in range(10)]
        for i, key in enumerate(keys):
            self.assertEqual(store.retrieve(key), f"obj{i}".encode())


if __name__ == "__main__":
    unittest.main()
