from django.core.management.base import BaseCommand

from apps.knowledge.models import Document


class Command(BaseCommand):
    help = "Re-embed documents by resetting their status"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-embed all documents")

    def handle(self, *args, **options):
        qs = Document.objects.all() if options["all"] else Document.objects.filter(status="failed")
        count = qs.count()
        qs.update(status="pending")
        self.stdout.write(self.style.SUCCESS(f"Reset {count} documents for re-embedding"))
