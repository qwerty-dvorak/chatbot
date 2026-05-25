from .models import Memory, MemorySettings


def get_user_memories(user, query=None, top_k=5):
    memories = Memory.objects.filter(user=user).order_by("-importance", "-last_used_at")[:top_k]
    if query:
        memories = [m for m in memories if query.lower() in m.content.lower()]
    return memories


def save_memory(user, content, importance=1):
    return Memory.objects.create(
        user=user,
        content=content,
        importance=importance,
    )


def get_memory_settings(user):
    settings, _ = MemorySettings.objects.get_or_create(user=user)
    return settings
