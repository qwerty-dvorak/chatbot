import logging

from apps.knowledge.models import Document

from .chunking import TextChunker
from .extractors.image import ImageExtractor
from .extractors.pdf import PDFExtractor
from .extractors.text import TextExtractor
from .models import IngestionJob

logger = logging.getLogger(__name__)

EXTRACTORS = [TextExtractor(), PDFExtractor(), ImageExtractor()]


def run_ingestion(job: IngestionJob) -> bool:
    document = job.document
    try:
        job.status = IngestionJob.Status.RUNNING
        job.attempts += 1
        job.save(update_fields=["status", "attempts"])

        extractor = _get_extractor(document.mime_type)
        if not extractor:
            raise ValueError(f"No extractor for MIME type: {document.mime_type}")

        result = extractor.extract(document)
        text = result.get("text", "")

        document.extracted_text = text
        document.status = Document.Status.READY
        document.save(update_fields=["extracted_text", "status"])

        chunker = TextChunker()
        chunks = chunker.chunk(document, text)
        if chunks:
            from apps.knowledge.models import DocumentChunk

            DocumentChunk.objects.filter(document=document).delete()
            DocumentChunk.objects.bulk_create(
                [DocumentChunk(**c) for c in chunks]
            )

        job.status = IngestionJob.Status.SUCCEEDED
        job.finished_at = __import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc)
        job.save(update_fields=["status", "finished_at"])
        logger.info(f"Ingestion succeeded for document {document.id}")
        return True

    except Exception as e:
        logger.error(f"Ingestion failed for document {document.id}: {e}")
        job.status = IngestionJob.Status.FAILED
        job.error = str(e)
        job.finished_at = __import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc)
        job.save(update_fields=["status", "error", "finished_at"])
        return False


def _get_extractor(mime_type: str):
    for extractor in EXTRACTORS:
        if extractor.can_handle(mime_type):
            return extractor
    return None
