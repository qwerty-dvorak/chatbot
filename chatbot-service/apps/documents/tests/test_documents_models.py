from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User
from apps.documents.models import (
    ArtifactRevision,
    ContentBlob,
    DocumentGrant,
    DocumentReference,
    EmbeddingSet,
)


class ContentBlobTest(TestCase):
    def setUp(self) -> None:
        self.blob = ContentBlob.objects.create(
            content_hash="a" * 64,
            mime_type="text/plain",
            size_bytes=100,
            object_key="blobs/a/a" * 4,
            storage_status=ContentBlob.StorageStatus.STORED,
        )

    def test_create_blob(self) -> None:
        self.assertEqual(self.blob.content_hash, "a" * 64)
        self.assertEqual(self.blob.mime_type, "text/plain")
        self.assertEqual(self.blob.size_bytes, 100)
        self.assertEqual(self.blob.storage_status, "stored")

    def test_unique_content_hash(self) -> None:
        with self.assertRaises(IntegrityError):
            ContentBlob.objects.create(
                content_hash="a" * 64,
                mime_type="text/plain",
            )

    def test_default_storage_status(self) -> None:
        blob = ContentBlob.objects.create(
            content_hash="b" * 64, mime_type="image/png"
        )
        self.assertEqual(blob.storage_status, ContentBlob.StorageStatus.PENDING)

    def test_string_representation(self) -> None:
        self.assertIn(self.blob.content_hash[:12], str(self.blob))
        self.assertIn("text/plain", str(self.blob))


class ArtifactRevisionTest(TestCase):
    def setUp(self) -> None:
        self.blob = ContentBlob.objects.create(
            content_hash="c" * 64, mime_type="application/pdf"
        )
        self.revision = ArtifactRevision.objects.create(
            blob=self.blob,
            pipeline_fingerprint="fp_v1_abc123",
            pipeline_version="1.0",
            extracted_text="Sample extracted text.",
            summary="A test summary.",
            processing_status=ArtifactRevision.ProcessingStatus.READY,
        )

    def test_create_revision(self) -> None:
        self.assertEqual(self.revision.blob, self.blob)
        self.assertEqual(self.revision.pipeline_fingerprint, "fp_v1_abc123")
        self.assertEqual(self.revision.processing_status, "ready")

    def test_unique_blob_pipeline(self) -> None:
        with self.assertRaises(IntegrityError):
            ArtifactRevision.objects.create(
                blob=self.blob,
                pipeline_fingerprint="fp_v1_abc123",
            )

    def test_default_processing_status(self) -> None:
        new_blob = ContentBlob.objects.create(
            content_hash="d" * 64, mime_type="text/markdown"
        )
        revision = ArtifactRevision.objects.create(
            blob=new_blob,
            pipeline_fingerprint="fp_v2_def456",
        )
        self.assertEqual(revision.processing_status, ArtifactRevision.ProcessingStatus.PENDING)

    def test_revision_to_string(self) -> None:
        self.assertIn("fp_v1_abc123", str(self.revision))

    def test_cascade_delete_blob(self) -> None:
        blob_id = self.blob.id
        self.blob.delete()
        self.assertFalse(ArtifactRevision.objects.filter(blob_id=blob_id).exists())


class DocumentReferenceTest(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="owner@example.com", password="pass123"
        )
        self.blob = ContentBlob.objects.create(
            content_hash="e" * 64, mime_type="text/plain"
        )
        self.revision = ArtifactRevision.objects.create(
            blob=self.blob, pipeline_fingerprint="fp_doc_ref"
        )
        self.doc_ref = DocumentReference.objects.create(
            owner=self.owner,
            artifact_revision=self.revision,
            title="Test Document",
            kind=DocumentReference.Kind.KNOWLEDGE,
        )

    def test_create_document_reference(self) -> None:
        self.assertEqual(self.doc_ref.owner, self.owner)
        self.assertEqual(self.doc_ref.title, "Test Document")
        self.assertEqual(self.doc_ref.kind, "knowledge")

    def test_chat_attachment_kind(self) -> None:
        att = DocumentReference.objects.create(
            owner=self.owner,
            artifact_revision=self.revision,
            title="Chat Attachment",
            kind=DocumentReference.Kind.CHAT_ATTACHMENT,
        )
        self.assertEqual(att.kind, "chat_attachment")


class DocumentGrantTest(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="owner@example.com", password="pass123"
        )
        self.viewer = User.objects.create_user(
            email="viewer@example.com", password="pass123"
        )
        self.editor = User.objects.create_user(
            email="editor@example.com", password="pass123"
        )
        self.blob = ContentBlob.objects.create(
            content_hash="f" * 64, mime_type="text/plain"
        )
        self.revision = ArtifactRevision.objects.create(
            blob=self.blob, pipeline_fingerprint="fp_grant"
        )
        self.doc_ref = DocumentReference.objects.create(
            owner=self.owner,
            artifact_revision=self.revision,
            title="Grant Test",
            kind=DocumentReference.Kind.KNOWLEDGE,
        )

    def test_create_viewer_grant(self) -> None:
        grant = DocumentGrant.objects.create(
            document_reference=self.doc_ref,
            user=self.viewer,
            role=DocumentGrant.Role.VIEWER,
        )
        self.assertEqual(grant.role, "viewer")
        self.assertEqual(grant.user, self.viewer)

    def test_create_editor_grant(self) -> None:
        grant = DocumentGrant.objects.create(
            document_reference=self.doc_ref,
            user=self.editor,
            role=DocumentGrant.Role.EDITOR,
        )
        self.assertEqual(grant.role, "editor")

    def test_unique_document_grant(self) -> None:
        DocumentGrant.objects.create(
            document_reference=self.doc_ref,
            user=self.viewer,
            role=DocumentGrant.Role.VIEWER,
        )
        with self.assertRaises(IntegrityError):
            DocumentGrant.objects.create(
                document_reference=self.doc_ref,
                user=self.viewer,
                role=DocumentGrant.Role.EDITOR,
            )

    def test_grant_query_by_user(self) -> None:
        DocumentGrant.objects.create(
            document_reference=self.doc_ref,
            user=self.viewer,
            role=DocumentGrant.Role.VIEWER,
        )
        grants = DocumentGrant.objects.filter(user=self.viewer)
        self.assertEqual(grants.count(), 1)
        self.assertEqual(grants.first().document_reference, self.doc_ref)

    def test_cascade_delete_document_reference(self) -> None:
        DocumentGrant.objects.create(
            document_reference=self.doc_ref,
            user=self.viewer,
            role=DocumentGrant.Role.VIEWER,
        )
        ref_id = self.doc_ref.id
        self.doc_ref.delete()
        self.assertFalse(DocumentGrant.objects.filter(document_reference_id=ref_id).exists())

    def test_acl_isolation_owner_can_view_all(self) -> None:
        """Owner should always have access; grants are for additional users."""
        grants = self.doc_ref.grants.all()
        self.assertEqual(grants.count(), 0)
        # Owner access is implicit — not stored in grants


class EmbeddingSetTest(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="owner@example.com", password="pass123"
        )
        self.blob = ContentBlob.objects.create(
            content_hash="g" * 64, mime_type="text/plain"
        )
        self.revision = ArtifactRevision.objects.create(
            blob=self.blob, pipeline_fingerprint="fp_embed"
        )

    def test_create_text_embedding_set(self) -> None:
        embed = EmbeddingSet.objects.create(
            artifact_revision=self.revision,
            model_name="llama-embed-nemotron-8b",
            model_version="1.0",
            embedding_type=EmbeddingSet.EmbeddingType.TEXT,
            dimensions=4096,
            milvus_collection="rag_text_chunks",
            milvus_ids=["id1", "id2", "id3"],
        )
        self.assertEqual(embed.dimensions, 4096)
        self.assertEqual(len(embed.milvus_ids), 3)
        self.assertEqual(embed.embedding_type, "text")

    def test_create_multimodal_embedding_set(self) -> None:
        embed = EmbeddingSet.objects.create(
            artifact_revision=self.revision,
            model_name="nemotron-colembed-vl-8b-v2",
            embedding_type=EmbeddingSet.EmbeddingType.MULTIMODAL,
            dimensions=4096,
            milvus_collection="rag_image_chunks",
        )
        self.assertEqual(embed.embedding_type, "multimodal")

    def test_unique_revision_embedding(self) -> None:
        EmbeddingSet.objects.create(
            artifact_revision=self.revision,
            model_name="test-model",
            embedding_type=EmbeddingSet.EmbeddingType.TEXT,
            dimensions=768,
            milvus_collection="test",
        )
        with self.assertRaises(IntegrityError):
            EmbeddingSet.objects.create(
                artifact_revision=self.revision,
                model_name="test-model",
                embedding_type=EmbeddingSet.EmbeddingType.TEXT,
                dimensions=768,
                milvus_collection="test",
            )

    def test_empty_milvus_ids_default(self) -> None:
        embed = EmbeddingSet.objects.create(
            artifact_revision=self.revision,
            model_name="test-model",
            embedding_type=EmbeddingSet.EmbeddingType.TEXT,
            dimensions=768,
            milvus_collection="test",
        )
        self.assertEqual(embed.milvus_ids, [])
