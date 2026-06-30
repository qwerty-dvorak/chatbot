import logging

from django.core.management.base import BaseCommand

from apps.documents.models import ArtifactRevision
from apps.ingestion.models import IngestionJob
from apps.knowledge.rag_client import rag_client

logger = logging.getLogger(__name__)

RAG_STATUS_MAP = {
    "queued": "pending",
    "running": "processing",
    "succeeded": "ready",
    "failed": "failed",
    "cancelled": "failed",
}


class Command(BaseCommand):
    help = "Sync RAG pipeline ingestion job status to local IngestionJob records."

    def handle(self, *args, **options) -> None:  # noqa: C901
        if not rag_client.is_enabled():
            self.stdout.write("RAG API not enabled, skipping sync.")
            return

        pending_jobs = IngestionJob.objects.filter(
            status__in=[IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING],
        ).select_related("document_reference__artifact_revision")

        synced = 0
        for job in pending_jobs:
            rag_job_id = (job.metadata or {}).get("rag_job_id")
            if not rag_job_id:
                continue

            rag_data = rag_client.get_job(rag_job_id)
            if not rag_data:
                continue

            rag_status = rag_data.get("status")
            if not rag_status or rag_status == job.status:
                continue

            rev = job.document_reference.artifact_revision
            done = False

            if rag_status == "succeeded":
                job.status = IngestionJob.Status.SUCCEEDED
                if rag_data.get("result"):
                    job.metadata["rag_result"] = rag_data["result"]
                rev.processing_status = ArtifactRevision.ProcessingStatus.READY
                done = True
            elif rag_status == "failed":
                job.status = IngestionJob.Status.FAILED
                job.error = rag_data.get("error", "")
                rev.processing_status = ArtifactRevision.ProcessingStatus.FAILED
                done = True
            elif rag_status == "running":
                job.status = IngestionJob.Status.RUNNING
                rev.processing_status = ArtifactRevision.ProcessingStatus.PROCESSING

            if done:
                job.finished_at = rag_data.get("completed_at")
            job.save(update_fields=["status", "metadata", "error", "finished_at"])
            rev.save(update_fields=["processing_status"])
            synced += 1
            logger.info(
                "Synced job %s: local=%s -> rag=%s",
                job.id, job.status, rag_status,
            )

        self.stdout.write(f"Synced {synced} RAG job(s).")
