import logging

from django.conf import settings

from apps.llm import milvus_store as milvus
from apps.llm.embeddings import FakeEmbeddingClient

from .models import Memory, MemorySettings

logger = logging.getLogger(__name__)


def get_user_memories(user, query=None, top_k=5):
    if query:
        try:
            embedder = FakeEmbeddingClient()
            query_vector = embedder.embed_query(query)
            results = milvus.search_vectors(
                settings.MILVUS_COLLECTION_MEMORIES,
                query_vector,
                top_k=top_k,
                expr=f'user_id == "{user.id}"',
            )
            if results:
                memories = []
                for r in results:
                    try:
                        mem = Memory.objects.get(id=r.get("id"))
                        memories.append(mem)
                    except Memory.DoesNotExist:
                        continue
                return memories
        except Exception:
            logger.warning("Milvus search failed, falling back to text search")

        texts = Memory.objects.filter(
            user=user, content__icontains=query
        ).order_by("-importance", "-last_used_at")[:top_k]
        return list(texts)

    return list(Memory.objects.filter(user=user).order_by("-importance", "-last_used_at")[:top_k])


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
        embedder = FakeEmbeddingClient()
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
    except Exception:
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
