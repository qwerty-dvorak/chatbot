import hashlib
import logging
import mimetypes
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.documents.models import ArtifactRevision, ContentBlob, DocumentReference
from apps.ingestion.models import IngestionJob
from apps.knowledge.models import KnowledgeDocument, KnowledgeSource
from apps.knowledge.rag_client import rag_client

logger = logging.getLogger(__name__)
User = get_user_model()

SEED_DATA_OWNER_EMAIL = "seed-data@barc.gov.in"


def _ensure_system_owner() -> User:
    user, _ = User.objects.get_or_create(
        email=SEED_DATA_OWNER_EMAIL,
        defaults={"is_active": True},
    )
    return user


class Command(BaseCommand):
    help = "Ingest all files from SEED_DATA_ROOT as global knowledge."

    def handle(self, *args, **options):
        root = getattr(settings, "SEED_DATA_ROOT", None)
        if not root or not os.path.isdir(root):
            self.stdout.write(self.style.WARNING(f"SEED_DATA_ROOT not found: {root}"))
            return

        global_source, _ = KnowledgeSource.objects.get_or_create(
            source_type="global",
            name="seed_data",
            defaults={"is_global": True, "visibility": "global"},
        )
        if not global_source.is_global:
            global_source.is_global = True
            global_source.save(update_fields=["is_global"])
        if global_source.visibility != "global":
            global_source.visibility = "global"
            global_source.save(update_fields=["visibility"])

        ingested = 0
        skipped = 0
        for fname in sorted(os.listdir(root)):
            fpath = os.path.join(root, fname)
            if not os.path.isfile(fpath):
                continue

            if fname == ".gitignore":
                continue

            with open(fpath, "rb") as f:
                sha256 = hashlib.sha256(f.read()).hexdigest()

            existing = KnowledgeDocument.objects.filter(sha256=sha256).first()
            if existing and existing.source.is_global:
                self.stdout.write(f"  Skipping (already global): {fname}")
                skipped += 1
                continue

            self.stdout.write(f"  Ingesting: {fname} ...")

            doc_ref, job = self._create_chain(fpath, fname, sha256)

            result = rag_client.ingest(
                fpath,
                ocr_mode="paddleocr",
                generate_summary=True,
                document_reference_id=str(doc_ref.id),
            )
            if not result:
                self.stdout.write(self.style.WARNING(f"  Failed to queue: {fname}"))
                job.status = IngestionJob.Status.FAILED
                job.error = "RAG API ingest failed to queue"
                job.save(update_fields=["status", "error"])
                ingested += 1
                continue

            job_id = result.get("id")
            if not job_id:
                self.stdout.write(self.style.WARNING(f"  No job id returned for: {fname}"))
                job.status = IngestionJob.Status.FAILED
                job.error = "No job id from RAG API"
                job.save(update_fields=["status", "error"])
                ingested += 1
                continue

            job.metadata["rag_job_id"] = job_id
            job.save(update_fields=["metadata"])

            job_result = rag_client.poll_job(job_id, max_retries=120, interval=2.0)
            if not job_result or job_result.get("status") != "succeeded":
                self.stdout.write(self.style.WARNING(f"  Ingestion did not succeed: {fname}"))
                job.status = IngestionJob.Status.FAILED
                job.error = job_result.get("error", "poll timed out or failed") if job_result else "poll timed out"
                job.save(update_fields=["status", "error"])
                ingested += 1
                continue

            doc = KnowledgeDocument.objects.filter(sha256=sha256).order_by("-created_at").first()
            if doc and doc.source != global_source:
                doc.source = global_source
                doc.save(update_fields=["source"])

            self._update_chain_result(job, job_result)
            self.stdout.write(f"  OK: {fname}")
            ingested += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. {ingested} ingested, {skipped} skipped (already global)."
        ))

    def _create_chain(self, fpath: str, fname: str, sha256: str) -> tuple[DocumentReference, IngestionJob]:
        blob, _ = ContentBlob.objects.get_or_create(
            content_hash=sha256,
            defaults={
                "mime_type": mimetypes.guess_type(fname)[0] or "application/octet-stream",
                "size_bytes": os.path.getsize(fpath),
                "object_key": fpath,
                "storage_status": ContentBlob.StorageStatus.STORED,
            },
        )
        revision, _ = ArtifactRevision.objects.get_or_create(
            blob=blob,
            pipeline_fingerprint="global_seed",
            defaults={
                "processing_status": ArtifactRevision.ProcessingStatus.PENDING,
            },
        )
        owner = _ensure_system_owner()
        doc_ref, _ = DocumentReference.objects.get_or_create(
            artifact_revision=revision,
            kind=DocumentReference.Kind.KNOWLEDGE,
            defaults={"owner": owner, "title": fname},
        )
        job, created = IngestionJob.objects.get_or_create(
            document_reference=doc_ref,
            status=IngestionJob.Status.QUEUED,
            defaults={"metadata": {}},
        )
        if not created:
            job.status = IngestionJob.Status.QUEUED
            job.metadata = {}
            job.error = ""
            job.save(update_fields=["status", "metadata", "error"])
        return doc_ref, job

    def _update_chain_result(self, job: IngestionJob, job_result: dict) -> None:
        primary = job_result.get("result") or {}
        first_result = (primary.get("results") or [None])[0] or {}
        job.status = IngestionJob.Status.SUCCEEDED
        job.metadata = {
            "rag_job_id": job_result.get("id"),
            "rag_result": first_result,
            "rag_timing": first_result.get("timing") or {},
            "rag_steps": job_result.get("steps") or [],
            "rag_step_summary": job_result.get("step_summary") or {},
        }
        job.save(update_fields=["status", "metadata"])
        revision = job.document_reference.artifact_revision
        revision.processing_status = ArtifactRevision.ProcessingStatus.READY
        revision.save(update_fields=["processing_status"])
