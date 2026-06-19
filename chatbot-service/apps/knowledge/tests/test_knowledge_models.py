import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.knowledge.models import Document, DocumentAsset, DocumentChunk, KnowledgeSource

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "sample_data", "text")

def _sample(name: str) -> SimpleUploadedFile:
    path = os.path.join(SAMPLE_DIR, name)
    with open(path, "rb") as f:
        return SimpleUploadedFile(name, f.read(), content_type="text/plain")


class KnowledgeUploadContractTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@example.com", password="pass123")
        self.client.login(username=self.user.email, password="pass123")

    def test_multiple_files_create_independent_documents(self):
        geology = _sample("topic_geology.txt")
        biology = _sample("topic_biology.txt")
        response = self.client.post(reverse("knowledge:upload"), {
            "files": [geology, biology],
            "visibility": "private", "ocr_mode": "none",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        documents = list(Document.objects.filter(owner=self.user).order_by("original_filename"))
        self.assertEqual([doc.original_filename for doc in documents], ["topic_biology.txt", "topic_geology.txt"])
        self.assertTrue(all(doc.ocr_mode == "none" for doc in documents))

    def test_batch_is_rejected_atomically_when_one_mime_type_is_unsupported(self):
        response = self.client.post(reverse("knowledge:upload"), {
            "files": [
                SimpleUploadedFile("good.txt", b"good", content_type="text/plain"),
                SimpleUploadedFile("bad.exe", b"bad", content_type="application/x-msdownload"),
            ]
        })
        self.assertContains(response, "not supported")
        self.assertFalse(Document.objects.exists())

    def test_image_asset_schema_keeps_traceability_fields(self):
        source = KnowledgeSource.objects.create(owner=self.user, name="pipeline", source_type="api")
        document = Document.objects.create(source=source, title="scan", mime_type="image/png", ocr_mode="paddleocr")
        asset = DocumentAsset.objects.create(
            document=document, asset_type="image", source_index=2, derived_index=4,
            object_key="a" * 64, sha256="b" * 64, width=1200, height=2400,
            ocr_backend="paddleocr", ocr_status="succeeded",
            preprocessing={"operations": ["rgb", "vertical_split:2"]}, text="invoice total",
        )
        asset.refresh_from_db()
        self.assertEqual((asset.source_index, asset.derived_index), (2, 4))
        self.assertEqual(asset.preprocessing["operations"][-1], "vertical_split:2")
        self.assertEqual(asset.text, "invoice total")

    def test_upload_form_hides_ocr_for_text_files_and_shows_for_images(self):
        response = self.client.get(reverse("knowledge:upload"))
        self.assertContains(response, 'id="ocr-group"')
        self.assertContains(response, 'style="display:none"')
        self.assertIn('needsOcr(f.name)', response.content.decode())

    def test_detail_view_shows_chunks_summary_and_timing(self):
        source = KnowledgeSource.objects.create(owner=self.user, name="test", source_type="upload")
        doc = Document.objects.create(
            source=source, owner=self.user, title="geology.txt",
            original_filename="topic_geology.txt", mime_type="text/plain",
            status="ready",
            analysis_summary="This document covers plate tectonics and mineral deposits.",
            metadata={
                "embedding_info": {"model": "nvidia/llama-embed-nemotron-8b", "dimension": 4096},
                "timing": {"chunk": 0.01, "embed": 0.5, "summary": 1.2, "total": 2.1},
            },
        )
        for i in range(3):
            DocumentChunk.objects.create(
                document=doc, chunk_index=i, content=f"This is chunk {i} about geology.",
                content_hash=f"hash{i}", token_count=10,
                metadata={"chunk_type": "text"},
            )
        response = self.client.get(reverse("knowledge:detail", args=[doc.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "3 chunk(s)")
        self.assertContains(response, "plate tectonics")
        self.assertContains(response, "nvidia/llama-embed-nemotron-8b")
        self.assertContains(response, "Chunk #0")
        self.assertContains(response, "Chunk #1")
        self.assertContains(response, "Chunk #2")
        self.assertContains(response, "embed 0.5s")
        self.assertContains(response, "summary 1.2s")
        self.assertContains(response, "total 2.1s")