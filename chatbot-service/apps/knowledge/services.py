from django.conf import settings

from apps.llm import milvus_store as milvus


def search_knowledge(query, user, top_k=8):
    results = milvus.search_vectors(
        settings.MILVUS_COLLECTION_CHUNKS,
        query,
        top_k=top_k,
    )
    return results
