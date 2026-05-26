from django.conf import settings

from apps.knowledge.models import DocumentChunk, RetrievalHit, RetrievalRun
from apps.tools.models import ToolCall


def hybrid_search(query, user, top_k=8, tool_call=None):
    from apps.llm.embeddings import FakeEmbeddingClient
    from apps.llm.milvus_store import search_vectors

    embedder = FakeEmbeddingClient()
    query_vector = embedder.embed_query(query)

    run = RetrievalRun.objects.create(
        user=user,
        query=query,
        strategy="hybrid",
        tool_call=tool_call,
    )

    results = search_vectors(
        settings.MILVUS_COLLECTION_CHUNKS,
        query_vector,
        top_k=top_k,
    )

    hits = []
    for rank, result in enumerate(results):
        chunk_id = result.get("id", "")
        score = result.get("distance", 0)
        try:
            chunk = DocumentChunk.objects.get(id=chunk_id)
        except DocumentChunk.DoesNotExist:
            continue
        hit = RetrievalHit.objects.create(
            run=run,
            chunk=chunk,
            rank=rank,
            score=score,
            source_title=chunk.document.title,
            source_metadata=chunk.metadata,
        )
        hits.append(hit)

    return hits


def vector_search(query, user, top_k=8):
    return hybrid_search(query, user, top_k=top_k)


def text_search(query, user, top_k=8):
    return hybrid_search(query, user, top_k=top_k)
