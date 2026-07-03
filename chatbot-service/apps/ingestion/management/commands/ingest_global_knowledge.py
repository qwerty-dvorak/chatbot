import hashlib
import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.knowledge.models import KnowledgeDocument, KnowledgeSource
from apps.knowledge.rag_client import rag_client

logger = logging.getLogger(__name__)


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
            defaults={"is_global": True, "visibility": "private"},
        )
        if not global_source.is_global:
            global_source.is_global = True
            global_source.save(update_fields=["is_global"])

        ingested = 0
        skipped = 0
        for fname in sorted(os.listdir(root)):
            fpath = os.path.join(root, fname)
            if not os.path.isfile(fpath):
                continue

            with open(fpath, "rb") as f:
                sha256 = hashlib.sha256(f.read()).hexdigest()

            existing = KnowledgeDocument.objects.filter(sha256=sha256).first()
            if existing and existing.source.is_global:
                self.stdout.write(f"  Skipping (already global): {fname}")
                skipped += 1
                continue

            self.stdout.write(f"  Ingesting: {fname} ...")
            result = rag_client.ingest(
                fpath,
                ocr_mode="paddleocr",
                generate_summary=True,
            )
            if not result:
                self.stdout.write(self.style.WARNING(f"  Failed to queue: {fname}"))
                continue

            job_id = result.get("id")
            if not job_id:
                self.stdout.write(self.style.WARNING(f"  No job id returned for: {fname}"))
                continue

            job_result = rag_client.poll_job(job_id, max_retries=120, interval=2.0)
            if not job_result or job_result.get("status") != "succeeded":
                self.stdout.write(self.style.WARNING(f"  Ingestion did not succeed: {fname}"))
                continue

            doc = KnowledgeDocument.objects.filter(sha256=sha256).order_by("-created_at").first()
            if doc:
                if doc.source != global_source:
                    doc.source = global_source
                    doc.save(update_fields=["source"])
                self.stdout.write(f"  OK: {fname}")
                ingested += 1
            else:
                self.stdout.write(self.style.WARNING(f"  No KnowledgeDocument found for: {fname}"))

        self.stdout.write(self.style.SUCCESS(
            f"Done. {ingested} ingested, {skipped} skipped (already global)."
        ))
