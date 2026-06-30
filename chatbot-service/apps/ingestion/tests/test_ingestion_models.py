from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User
from apps.documents.models import ArtifactRevision, ContentBlob, DocumentReference
from apps.ingestion.models import IngestionJob, IngestionStepAttempt


class IngestionStepAttemptTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="user@test.com", password="pass")
        self.blob = ContentBlob.objects.create(
            content_hash="a" * 64,
            mime_type="text/plain",
            size_bytes=100,
            storage_status=ContentBlob.StorageStatus.STORED,
        )
        self.revision = ArtifactRevision.objects.create(
            blob=self.blob,
            pipeline_fingerprint="test123",
        )
        self.doc_ref = DocumentReference.objects.create(
            owner=self.user,
            artifact_revision=self.revision,
            title="test.txt",
            kind=DocumentReference.Kind.KNOWLEDGE,
        )
        self.job = IngestionJob.objects.create(document_reference=self.doc_ref)

    def test_create_step_attempt(self) -> None:
        step = IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EXTRACT,
            attempt_number=1,
            status=IngestionStepAttempt.Status.RUNNING,
        )
        self.assertEqual(step.step_type, "extract")
        self.assertEqual(step.status, "running")
        self.assertEqual(step.attempt_number, 1)

    def test_step_default_status(self) -> None:
        step = IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.CHUNK,
        )
        self.assertEqual(step.status, IngestionStepAttempt.Status.QUEUED)

    def test_unique_job_step_attempt(self) -> None:
        IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EXTRACT,
            attempt_number=1,
        )
        with self.assertRaises(IntegrityError):
            IngestionStepAttempt.objects.create(
                job=self.job,
                step_type=IngestionStepAttempt.StepType.EXTRACT,
                attempt_number=1,
            )

    def test_all_step_types(self) -> None:
        types = [
            IngestionStepAttempt.StepType.EXTRACT,
            IngestionStepAttempt.StepType.OCR,
            IngestionStepAttempt.StepType.CHUNK,
            IngestionStepAttempt.StepType.SUMMARIZE,
            IngestionStepAttempt.StepType.HYPOTHETICAL_QUESTIONS,
            IngestionStepAttempt.StepType.EMBED_TEXT,
            IngestionStepAttempt.StepType.EMBED_MULTIMODAL,
            IngestionStepAttempt.StepType.INDEX,
        ]
        for step_type in types:
            step = IngestionStepAttempt.objects.create(
                job=self.job, step_type=step_type
            )
            self.assertEqual(step.step_type, step_type)

    def test_all_status_types(self) -> None:
        statuses = [
            IngestionStepAttempt.Status.QUEUED,
            IngestionStepAttempt.Status.RUNNING,
            IngestionStepAttempt.Status.SUCCEEDED,
            IngestionStepAttempt.Status.FAILED,
            IngestionStepAttempt.Status.SKIPPED,
        ]
        for i, status in enumerate(statuses):
            step = IngestionStepAttempt.objects.create(
                job=self.job,
                step_type=IngestionStepAttempt.StepType.INDEX,
                attempt_number=i + 1,
                status=status,
            )
            self.assertEqual(step.status, status)

    def test_step_with_duration(self) -> None:
        step = IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EMBED_TEXT,
            duration_ms=1500,
            status=IngestionStepAttempt.Status.SUCCEEDED,
        )
        self.assertEqual(step.duration_ms, 1500)

    def test_step_with_error(self) -> None:
        step = IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.OCR,
            status=IngestionStepAttempt.Status.FAILED,
            error="OCR service timeout",
        )
        self.assertEqual(step.error, "OCR service timeout")

    def test_multiple_attempts_on_same_step(self) -> None:
        IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EXTRACT,
            attempt_number=1,
            status=IngestionStepAttempt.Status.FAILED,
        )
        IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EXTRACT,
            attempt_number=2,
            status=IngestionStepAttempt.Status.SUCCEEDED,
        )
        attempts = IngestionStepAttempt.objects.filter(
            job=self.job, step_type=IngestionStepAttempt.StepType.EXTRACT
        ).order_by("attempt_number")
        self.assertEqual(attempts.count(), 2)
        self.assertEqual(attempts[0].status, "failed")
        self.assertEqual(attempts[1].status, "succeeded")

    def test_step_accessible_from_job(self) -> None:
        IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EXTRACT,
        )
        IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.CHUNK,
        )
        self.assertEqual(self.job.step_attempts.count(), 2)

    def test_cascade_delete_job(self) -> None:
        IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.EXTRACT,
        )
        job_id = self.job.id
        self.job.delete()
        self.assertFalse(
            IngestionStepAttempt.objects.filter(job_id=job_id).exists()
        )

    def test_step_string_representation(self) -> None:
        step = IngestionStepAttempt.objects.create(
            job=self.job,
            step_type=IngestionStepAttempt.StepType.CHUNK,
            status=IngestionStepAttempt.Status.RUNNING,
        )
        self.assertIn("chunk", str(step))
        self.assertIn("running", str(step))
