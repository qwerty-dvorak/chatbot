from django.conf import settings

from .models import ChatCompaction


def compact_chat(chat):
    from apps.chat.models import Message

    messages = Message.objects.filter(chat=chat, status=Message.Status.COMPLETED).order_by("created_at")
    if messages.count() < 10:
        return None

    oldest_ids = list(messages.values_list("id", flat=True)[:messages.count() // 2])
    if not oldest_ids:
        return None
    from_msg = Message.objects.get(id=oldest_ids[0])
    to_msg = Message.objects.get(id=oldest_ids[-1])
    fake_summary_text = f"Summary of {len(oldest_ids)} messages in {chat.title}"

    compaction = ChatCompaction.objects.create(
        chat=chat,
        from_message=from_msg,
        to_message=to_msg,
        summary=fake_summary_text,
        facts=[],
        open_questions=[],
        token_count=len(fake_summary_text) // 4,
        model=settings.QWEN_CHAT_MODEL,
    )
    return compaction
