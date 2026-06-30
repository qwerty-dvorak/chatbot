import logging
import re

from django.conf import settings
from django.db.models import Q

from apps.llm import milvus_store as milvus
from apps.llm.embeddings import EmbeddingClient

from .models import Memory, MemorySettings

logger = logging.getLogger(__name__)


def get_user_memories(user, query=None, top_k=5):
    """
    Search user memories using multiple strategies:

    1. Vector/semantic search via Milvus (if available)
    2. Keyword search — split query into words, match any word via __icontains
    3. Full-query substring search as final fallback
    Results are merged, deduplicated, and sorted by relevance.
    """
    if not query:
        return list(Memory.objects.filter(user=user).order_by("-importance", "-last_used_at")[:top_k])

    seen = set()
    results = []

    # Strategy 1: Vector (semantic) search
    vector_memories = _search_vectors(user, query, top_k)
    for m in vector_memories:
        if m.id not in seen:
            seen.add(m.id)
            results.append(m)

    # Strategy 2: Multi-keyword search — any word from the query matches
    keyword_memories = _search_keywords(user, query, top_k)
    for m in keyword_memories:
        if m.id not in seen and len(results) < top_k:
            seen.add(m.id)
            results.append(m)

    return results


def _search_vectors(user, query, top_k):
    try:
        embedder = EmbeddingClient()
        query_vector = embedder.embed_query(query)
        milvus_results = milvus.search_vectors(
            settings.MILVUS_COLLECTION_MEMORIES,
            query_vector,
            top_k=top_k,
            expr=f'user_id == "{user.id}"',
        )
        if milvus_results:
            memories = []
            for r in milvus_results:
                try:
                    mem = Memory.objects.get(id=r.get("id"))
                    memories.append(mem)
                except Memory.DoesNotExist:
                    continue
            return memories
    except Exception:  # noqa: BLE001
        logger.warning("Milvus vector search failed")
    return []


def _search_keywords(user, query, top_k):
    """Split query into words, find memories matching ANY word, rank by match count."""
    words = [w for w in re.split(r"\s+", query.strip()) if len(w) > 1]
    if not words:
        return list(Memory.objects.filter(
            user=user, content__icontains=query
        ).order_by("-importance", "-last_used_at")[:top_k])

    q = Q()
    for word in words:
        q |= Q(content__icontains=word)

    matches = Memory.objects.filter(user=user).filter(q).order_by("-importance", "-last_used_at")
    scored = sorted(matches, key=lambda m: (
        -sum(1 for w in words if w.lower() in m.content.lower()),
        -m.importance,
    ))
    return list(scored[:top_k])


def save_memory(user, content, importance=1):
    memory = Memory.objects.create(
        user=user,
        content=content,
        importance=importance,
    )
    _index_memory(memory)
    return memory


def _index_memory(memory):
    try:
        embedder = EmbeddingClient()
        vector = embedder.embed_query(memory.content)
        milvus.insert_vectors(
            settings.MILVUS_COLLECTION_MEMORIES,
            [vector],
            [{
                "id": str(memory.id),
                "user_id": str(memory.user.id),
                "content": memory.content,
                "importance": memory.importance,
            }],
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to index memory in Milvus")


def reindex_memories(user=None):
    qs = Memory.objects.all()
    if user:
        qs = qs.filter(user=user)
    for mem in qs:
        _index_memory(mem)


def get_memory_settings(user):
    settings_obj, _ = MemorySettings.objects.get_or_create(user=user)
    return settings_obj
