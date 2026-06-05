import os

from django.core.management.base import BaseCommand

from apps.knowledge.models import Document, KnowledgeSource
from apps.ingestion.models import IngestionJob


class Command(BaseCommand):
    help = "Ingest files from a local path"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Path to file or directory")

    def handle(self, *args, **options):
        path = options["path"]
        if not os.path.exists(path):
            self.stdout.write(self.style.ERROR(f"Path does not exist: {path}"))
            return

        if os.path.isfile(path):
            self._ingest_file(path)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    self._ingest_file(fpath)

    def _ingest_file(self, fpath):
        import hashlib
        from django.core.files import File

        sha256 = hashlib.sha256(open(fpath, "rb").read()).hexdigest()
        if Document.objects.filter(sha256=sha256).exists():
            self.stdout.write(f"Skipping (duplicate): {fpath}")
            return

        source, _ = KnowledgeSource.objects.get_or_create(
            owner=None,
            name="CLI Import",
            defaults={"source_type": "folder", "visibility": "global"},
        )

        doc = Document.objects.create(
            source=source,
            title=os.path.basename(fpath),
            original_filename=os.path.basename(fpath),
            mime_type=self._guess_mime(fpath),
            status=Document.Status.PENDING,
            sha256=sha256,
        )

        from django.core.files.storage import default_storage
        with open(fpath, "rb") as f:
            path = default_storage.save(f"uploads/{os.path.basename(fpath)}", File(f))
        doc.file = path
        doc.save(update_fields=["file"])

        IngestionJob.objects.create(document=doc)
        self.stdout.write(f"Queued: {fpath}")

    def _guess_mime(self, fpath):
        ext = os.path.splitext(fpath)[1].lower()
        mime_map = {
            ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
            ".pdf": "application/pdf",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".html": "text/html", ".xml": "application/xml",
        }
        return mime_map.get(ext, "application/octet-stream")
