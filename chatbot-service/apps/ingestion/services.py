import os

from django.conf import settings

from apps.llm import milvus_store as milvus
from apps.llm.embeddings import EmbeddingClient

from .chunking import TextChunker
from .extractors.image import ImageExtractor
from .extractors.pdf import PDFExtractor
from .extractors.text import TextExtractor
from .models import IngestionJob

EXTRACTORS = [TextExtractor(), PDFExtractor(), ImageExtractor()]


def run_ingestion(job: IngestionJob) -> bool:
    doc_ref = job.document_reference
    revision = doc_ref.artifact_revision
    blob = revision.blob
    try:
        job.status = IngestionJob.Status.RUNNING
        job.save(update_fields=["status"])
        file_path = _resolve_path(blob.object_key)
        text = _extract_text(file_path, blob.mime_type, revision.extracted_text)
        revision.extracted_text = text
        revision.processing_status = "ready"
        revision.save(update_fields=["extracted_text", "processing_status"])
        chunker = TextChunker()
        chunks = chunker.chunk(text)
        _index_chunks(chunks, doc_ref)
        job.status = IngestionJob.Status.SUCCEEDED
        job.save(update_fields=["status", "finished_at"])
        return True
    except Exception as e:
        job.status = IngestionJob.Status.FAILED
        job.error = str(e)
        job.save(update_fields=["status", "error", "finished_at"])
        return False


def _extract_text(file_path: str, mime_type: str, existing_text: str = "") -> str:
    extractor = _get_extractor(mime_type)
    if not extractor:
        raise ValueError(f"No extractor for MIME type: {mime_type}")
    result = extractor.extract(file_path, mime_type, existing_text)
    return result.get("text", "")


def _resolve_path(object_key: str) -> str:
    if os.path.isabs(object_key):
        return object_key
    docs_root = getattr(settings, "DOCS_ROOT", os.path.join(settings.MEDIA_ROOT, "docs"))
    return os.path.join(docs_root, object_key)


def _index_chunks(chunks, doc_ref):
    if not chunks:
        return
    try:
        embedder = EmbeddingClient()
        texts = [c["content"] for c in chunks]
        vectors = embedder.embed(texts)
        milvus.insert_vectors(
            settings.MILVUS_COLLECTION_CHUNKS,
            vectors,
            chunks,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to index chunks in Milvus")


def _get_extractor(mime_type: str):
    for extractor in EXTRACTORS:
        if extractor.can_handle(mime_type):
            return extractor
    return None