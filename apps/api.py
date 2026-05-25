from django.http import JsonResponse


def health_check(request):
    return JsonResponse({"status": "ok"})


def stats(request):
    from apps.accounts.models import User
    from apps.chat.models import Chat, Message
    from apps.knowledge.models import Document, DocumentChunk
    from apps.llm.models import TokenUsage

    return JsonResponse({
        "users": User.objects.count(),
        "chats": Chat.objects.count(),
        "messages": Message.objects.count(),
        "documents": Document.objects.count(),
        "chunks": DocumentChunk.objects.count(),
        "token_usage_entries": TokenUsage.objects.count(),
    })
