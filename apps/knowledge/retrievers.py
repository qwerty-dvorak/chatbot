from apps.knowledge.models import DocumentChunk, RetrievalHit, RetrievalRun
from apps.tools.models import ToolCall


def hybrid_search(query, user, top_k=8, tool_call=None):
    from apps.llm.embeddings import FakeEmbeddingClient

    embedder = FakeEmbeddingClient(dim=256)
    query_vector = embedder.embed_text(query)

    run = RetrievalRun.objects.create(
        user=user,
        query=query,
        query_embedding=query_vector,
        strategy="hybrid",
        tool_call=tool_call,
    )

    chunks = DocumentChunk.objects.all()[:top_k]
    hits = []
    for rank, chunk in enumerate(chunks):
        hit = RetrievalHit.objects.create(
            run=run,
            chunk=chunk,
            rank=rank,
            score=1.0 - (rank * 0.1),
            source_title=chunk.document.title,
            source_metadata=chunk.metadata,
        )
        hits.append(hit)

    return hits


def vector_search(query, user, top_k=8):
    return hybrid_search(query, user, top_k=top_k)


def text_search(query, user, top_k=8):
    return hybrid_search(query, user, top_k=top_k)
