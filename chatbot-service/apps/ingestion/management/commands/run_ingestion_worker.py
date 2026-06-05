import logging
import time

from django.core.management.base import BaseCommand

from ...models import IngestionJob


class Command(BaseCommand):
    help = "Run ingestion worker loop processing queued jobs"

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=5, help="Poll interval in seconds")

    def handle(self, *args, **options):
        interval = options["interval"]
        self.stdout.write(f"Ingestion worker started, polling every {interval}s")

        while True:
            job = IngestionJob.objects.filter(status=IngestionJob.Status.QUEUED).first()
            if job:
                self.stdout.write(f"Processing job {job.id}")
                from ...services import run_ingestion
                success = run_ingestion(job)
                if success:
                    self.stdout.write(self.style.SUCCESS(f"Job {job.id} succeeded"))
                else:
                    self.stdout.write(self.style.ERROR(f"Job {job.id} failed"))
            time.sleep(interval)
