from django.core.management.base import BaseCommand

from apps.documents.models import ArtifactRevision


class Command(BaseCommand):
    help = "Re-embed documents by resetting their processing status"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-embed all revisions")

    def handle(self, *args, **options):
        qs = ArtifactRevision.objects.all() if options["all"] else ArtifactRevision.objects.filter(processing_status="failed")
        count = qs.count()
        qs.update(processing_status="pending")
        self.stdout.write(self.style.SUCCESS(f"Reset {count} revisions for re-embedding"))