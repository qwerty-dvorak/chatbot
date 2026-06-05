from django.test import TestCase

from apps.knowledge.models import Document, KnowledgeSource
from apps.ingestion.chunking import TextChunker
from apps.ingestion.extractors.base import BaseExtractor
from apps.ingestion.extractors.text import TextExtractor
from apps.ingestion.models import IngestionJob
from apps.ingestion.services import run_ingestion


class BaseExtractorTest(TestCase):
    def test_extractor_interface(self):
        class TestExtractor(BaseExtractor):
            def can_handle(self, mime_type):
                return mime_type == "test/foo"
            def extract(self, document):
                return {"text": "extracted", "metadata": {}}

        ext = TestExtractor()
        self.assertTrue(ext.can_handle("test/foo"))
        self.assertFalse(ext.can_handle("test/bar"))
        self.assertEqual(ext.extract_text(None), "extracted")


class TextExtractorTest(TestCase):
    def setUp(self):
        self.extractor = TextExtractor()

    def test_can_handle_text(self):
        self.assertTrue(self.extractor.can_handle("text/plain"))
        self.assertTrue(self.extractor.can_handle("text/markdown"))
        self.assertTrue(self.extractor.can_handle("text/csv"))
        self.assertFalse(self.extractor.can_handle("application/pdf"))

    def test_extract_returns_text(self):
        doc = Document.objects.create(
            source=KnowledgeSource.objects.create(name="Src"),
            title="Test", mime_type="text/plain",
        )
        result = self.extractor.extract(doc)
        self.assertIn("text", result)
        self.assertIn("metadata", result)


class TextChunkerTest(TestCase):
    def setUp(self):
        self.chunker = TextChunker(target_tokens=100, overlap_tokens=10)

    def test_chunk_empty_text(self):
        self.assertEqual(self.chunker.chunk(None, ""), [])

    def test_chunk_single_paragraph(self):
        doc = Document.objects.create(
            source=KnowledgeSource.objects.create(name="Src"),
            title="Test", mime_type="text/plain",
        )
        text = "Short paragraph."
        chunks = self.chunker.chunk(doc, text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["content"], "Short paragraph.")

    def test_chunk_multiple_paragraphs(self):
        doc = Document.objects.create(
            source=KnowledgeSource.objects.create(name="Src"),
            title="Test", mime_type="text/plain",
        )
        text = "A" * 500 + "\n\n" + "B" * 500
        chunks = self.chunker.chunk(doc, text)
        self.assertGreaterEqual(len(chunks), 1)

    def test_chunk_metadata(self):
        doc = Document.objects.create(
            source=KnowledgeSource.objects.create(name="Src"),
            title="Test Doc", mime_type="text/plain",
        )
        chunks = self.chunker.chunk(doc, "Some content here.")
        self.assertEqual(chunks[0]["metadata"]["title"], "Test Doc")

    def test_chunk_content_hash(self):
        doc = Document.objects.create(
            source=KnowledgeSource.objects.create(name="Src"),
            title="Test", mime_type="text/plain",
        )
        chunks = self.chunker.chunk(doc, "Consistent content.")
        self.assertEqual(len(chunks[0]["content_hash"]), 64)


class IngestionJobModelTest(TestCase):
    def setUp(self):
        from apps.accounts.models import User
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        source = KnowledgeSource.objects.create(owner=self.user, name="Source")
        self.doc = Document.objects.create(source=source, title="Test", mime_type="text/plain")

    def test_create_job(self):
        job = IngestionJob.objects.create(document=self.doc)
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.attempts, 0)

    def test_job_str(self):
        job = IngestionJob.objects.create(document=self.doc)
        self.assertIn("queued", str(job))


class IngestionServiceTest(TestCase):
    def setUp(self):
        from apps.accounts.models import User
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        source = KnowledgeSource.objects.create(owner=self.user, name="Source")
        self.doc = Document.objects.create(
            source=source, title="Test", mime_type="text/plain",
            extracted_text="Hello world ingestion test.",
        )

    def test_run_ingestion_success(self):
        job = IngestionJob.objects.create(document=self.doc)
        success = run_ingestion(job)
        self.assertTrue(success)
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, "ready")

    def test_run_ingestion_creates_chunks(self):
        job = IngestionJob.objects.create(document=self.doc)
        run_ingestion(job)
        from apps.knowledge.models import DocumentChunk
        chunks = DocumentChunk.objects.filter(document=self.doc)
        self.assertGreater(chunks.count(), 0)

    def test_run_ingestion_no_extractor(self):
        from apps.knowledge.models import Document
        doc = Document.objects.create(
            source=self.doc.source,
            title="Unknown",
            mime_type="application/octet-stream",
        )
        job = IngestionJob.objects.create(document=doc)
        success = run_ingestion(job)
        self.assertFalse(success)
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
