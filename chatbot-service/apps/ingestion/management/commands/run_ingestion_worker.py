import logging
import time

from django.core.management.base import BaseCommand

from ...models import IngestionJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run ingestion worker loop processing queued jobs"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=5, help="Poll interval in seconds")

    def _poll_rag_job(self, job):
        from apps.knowledge.rag_client import rag_client
        rag_job_id = job.metadata.get("rag_job_id")
        if not rag_job_id:
            return None
        self.stdout.write(f"  Polling RAG pipeline job {rag_job_id}")
        result = rag_client.poll_job(rag_job_id, max_retries=1, interval=1)
        if result is None:
            self.stdout.write(f"  RAG job {rag_job_id} still in progress")
            return None
        status = result.get("status")
        if status == "succeeded":
            job.document_reference.artifact_revision.processing_status = "ready"
            job.document_reference.artifact_revision.save(update_fields=["processing_status"])
            job.status = IngestionJob.Status.SUCCEEDED
            rag_result = result.get("result") or {}
            steps = result.get("steps") or []
            job.metadata["rag_result"] = {
                "document_id": rag_result.get("results", [{}])[0].get("document_id") if rag_result.get("results") else None,
                "chunks_created": rag_result.get("results", [{}])[0].get("chunks_created") if rag_result.get("results") else None,
                "embeddings_indexed": rag_result.get("results", [{}])[0].get("embeddings_indexed") if rag_result.get("results") else None,
                "summary_text": rag_result.get("results", [{}])[0].get("summary_text") if rag_result.get("results") else None,
                "step_summary": result.get("step_summary"),
            }
            job.metadata["rag_timing"] = {
                s["name"]: {
                    "started_at": s.get("started_at"),
                    "completed_at": s.get("completed_at"),
                    "duration_ms": round((s.get("completed_at", 0) - s.get("started_at", 0)) * 1000, 1) if s.get("started_at") and s.get("completed_at") else None,
                }
                for s in steps if s.get("status") == "completed" and s.get("started_at")
            }
            job.save(update_fields=["status", "finished_at", "metadata"])
            self.stdout.write(self.style.SUCCESS(f"  RAG job {rag_job_id} succeeded, status updated"))
            return True
        else:
            self.stdout.write(self.style.WARNING(f"  RAG job {rag_job_id} failed ({status}) — falling back to local ingestion"))
            job.metadata.pop("rag_job_id", None)
            job.save(update_fields=["metadata"])
            from ...services import run_ingestion
            success = run_ingestion(job)
            if success:
                self.stdout.write(self.style.SUCCESS(f"  Local fallback succeeded for job {job.id}"))
            else:
                self.stdout.write(self.style.ERROR(f"  Local fallback failed for job {job.id}"))
            return success

    def handle(self, *args, **options):
        interval = options["interval"]
        self.stdout.write(f"Ingestion worker started, polling every {interval}s")

        while True:
            job = IngestionJob.objects.filter(status=IngestionJob.Status.QUEUED).first()
            if job:
                self.stdout.write(f"Processing job {job.id}")
                if job.metadata and job.metadata.get("rag_job_id"):
                    self._poll_rag_job(job)
                else:
                    from ...services import run_ingestion
                    success = run_ingestion(job)
                    if success:
                        self.stdout.write(self.style.SUCCESS(f"Job {job.id} succeeded"))
                    else:
                        self.stdout.write(self.style.ERROR(f"Job {job.id} failed"))
                job.refresh_from_db()
                if job.status in (IngestionJob.Status.SUCCEEDED, IngestionJob.Status.FAILED):
                    self.stdout.write(f"Job {job.id} done ({job.status})")
            time.sleep(interval)
