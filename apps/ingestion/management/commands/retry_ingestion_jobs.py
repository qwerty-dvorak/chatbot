from django.core.management.base import BaseCommand

from ...models import IngestionJob


class Command(BaseCommand):
    help = "Retry failed ingestion jobs"

    def handle(self, *args, **options):
        failed = IngestionJob.objects.filter(
            status=IngestionJob.Status.FAILED, attempts__lt=3
        )
        count = failed.count()
        failed.update(status=IngestionJob.Status.QUEUED)
        self.stdout.write(self.style.SUCCESS(f"Retrying {count} failed ingestion jobs"))
