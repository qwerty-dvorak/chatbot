from django.core.management.base import BaseCommand

from apps.ingestion.models import IngestionJob


class Command(BaseCommand):
    help = "Retry failed ingestion jobs"

    def handle(self, *args, **options) -> None:
        failed = IngestionJob.objects.filter(
            status=IngestionJob.Status.FAILED, attempts__lt=3
        )
        count = failed.count()
        failed.update(status=IngestionJob.Status.QUEUED)
        self.stdout.write(self.style.SUCCESS(f"Retrying {count} failed ingestion jobs"))
