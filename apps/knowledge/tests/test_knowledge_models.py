import hashlib
import uuid

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.knowledge.models import Document, DocumentAsset, DocumentChunk, KnowledgeSource


class KnowledgeSourceModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")

    def test_create_source(self):
        source = KnowledgeSource.objects.create(
            owner=self.user, name="My Docs", source_type="upload", visibility="private"
        )
        self.assertEqual(source.name, "My Docs")
        self.assertEqual(source.visibility, "private")

    def test_source_str(self):
        source = KnowledgeSource.objects.create(name="Test Source")
        self.assertEqual(str(source), "Test Source")

    def test_default_visibility(self):
        source = KnowledgeSource.objects.create(owner=self.user, name="Default")
        self.assertEqual(source.visibility, "private")


class DocumentModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.source = KnowledgeSource.objects.create(owner=self.user, name="Source")

    def test_create_document(self):
        doc = Document.objects.create(
            source=self.source,
            owner=self.user,
            title="Test Doc",
            mime_type="text/plain",
            status=Document.Status.PENDING,
        )
        self.assertEqual(doc.title, "Test Doc")
        self.assertEqual(doc.status, "pending")

    def test_document_str(self):
        doc = Document.objects.create(source=self.source, title="My Doc", mime_type="text/plain")
        self.assertEqual(str(doc), "My Doc")

    def test_document_defaults(self):
        doc = Document.objects.create(source=self.source, title="Defaults", mime_type="text/plain")
        self.assertEqual(doc.status, "pending")
        self.assertEqual(doc.extracted_text, "")

    def test_document_status_ready(self):
        doc = Document.objects.create(
            source=self.source, title="Ready Doc", mime_type="text/plain", status=Document.Status.READY
        )
        self.assertEqual(doc.status, "ready")


class DocumentChunkModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.source = KnowledgeSource.objects.create(owner=self.user, name="Source")
        self.doc = Document.objects.create(source=self.source, title="Doc", mime_type="text/plain")

    def test_create_chunk(self):
        chunk = DocumentChunk.objects.create(
            document=self.doc,
            chunk_index=0,
            content="Hello world",
            content_hash=hashlib.sha256(b"Hello world").hexdigest(),
            token_count=3,
        )
        self.assertEqual(chunk.chunk_index, 0)
        self.assertEqual(chunk.content, "Hello world")

    def test_chunk_unique_index(self):
        DocumentChunk.objects.create(
            document=self.doc, chunk_index=0,
            content="First", content_hash="abc", token_count=1,
        )
        with self.assertRaises(Exception):
            DocumentChunk.objects.create(
                document=self.doc, chunk_index=0,
                content="Second", content_hash="def", token_count=1,
            )

    def test_chunk_str(self):
        chunk = DocumentChunk.objects.create(
            document=self.doc, chunk_index=0,
            content="Test", content_hash="xyz", token_count=1,
        )
        self.assertIn("Chunk 0", str(chunk))


class DocumentAssetModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.source = KnowledgeSource.objects.create(owner=self.user, name="Source")
        self.doc = Document.objects.create(source=self.source, title="Doc", mime_type="application/pdf")

    def test_create_asset(self):
        asset = DocumentAsset.objects.create(
            document=self.doc, asset_type="page", page_number=1
        )
        self.assertEqual(asset.asset_type, "page")
        self.assertEqual(asset.page_number, 1)

    def test_asset_str(self):
        asset = DocumentAsset.objects.create(document=self.doc, asset_type="image")
        self.assertIn("image", str(asset))


class DocumentListViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")
        self.source = KnowledgeSource.objects.create(owner=self.user, name="Source")

    def test_list_shows_user_documents(self):
        Document.objects.create(source=self.source, owner=self.user, title="My Doc", mime_type="text/plain")
        response = self.client.get(reverse("knowledge:list"))
        self.assertContains(response, "My Doc")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("knowledge:list"))
        self.assertEqual(response.status_code, 302)


class DocumentUploadViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username="test@example.com", password="pass123")

    def test_upload_page_loads(self):
        response = self.client.get(reverse("knowledge:upload"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "knowledge/upload.html")

    def test_upload_creates_document(self):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile

        content = b"Hello, this is a test document."
        uploaded = SimpleUploadedFile("test.txt", content, content_type="text/plain")
        response = self.client.post(reverse("knowledge:upload"), {
            "title": "Test Upload",
            "file": uploaded,
        }, follow=True)
        self.assertTrue(Document.objects.filter(title="Test Upload").exists())
        doc = Document.objects.get(title="Test Upload")
        self.assertEqual(doc.owner, self.user)
        self.assertEqual(doc.mime_type, "text/plain")
