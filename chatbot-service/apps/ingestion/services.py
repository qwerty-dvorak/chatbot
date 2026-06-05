import logging
from datetime import datetime, timezone

from django.conf import settings

from apps.knowledge.models import Document
from apps.llm import milvus_store as milvus
from apps.llm.embeddings import FakeEmbeddingClient

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
            created = DocumentChunk.objects.bulk_create(
                [DocumentChunk(**c) for c in chunks]
            )
            _index_chunks(created)

        job.status = IngestionJob.Status.SUCCEEDED
        job.finished_at = datetime.now(tz=timezone.utc)
        job.save(update_fields=["status", "finished_at"])
        logger.info(f"Ingestion succeeded for document {document.id}")
        return True

    except Exception as e:
        logger.error(f"Ingestion failed for document {document.id}: {e}")
        job.status = IngestionJob.Status.FAILED
        job.error = str(e)
        job.finished_at = datetime.now(tz=timezone.utc)
        job.save(update_fields=["status", "error", "finished_at"])
        return False


def _index_chunks(chunks):
    if not chunks:
        return
    try:
        embedder = FakeEmbeddingClient()
        texts = [c.content for c in chunks]
        vectors = embedder.embed(texts)
        metadata_list = []
        for chunk in chunks:
            metadata_list.append({
                "id": str(chunk.id),
                "document_id": str(chunk.document_id),
                "chunk_index": chunk.chunk_index,
                "content": chunk.content[:512],
                "token_count": chunk.token_count,
            })
        milvus.insert_vectors(
            settings.MILVUS_COLLECTION_CHUNKS,
            vectors,
            metadata_list,
        )
        logger.info(f"Indexed {len(chunks)} chunks in Milvus")
    except Exception:
        logger.warning("Failed to index chunks in Milvus")


def _get_extractor(mime_type: str):
    for extractor in EXTRACTORS:
        if extractor.can_handle(mime_type):
            return extractor
    return None
