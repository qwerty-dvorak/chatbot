from django.http import JsonResponse

from apps.documents.models import DocumentReference


def health_check(request):
    return JsonResponse({"status": "ok"})


def stats(request):
    from apps.accounts.models import User
    from apps.chat.models import Chat, Message

    return JsonResponse({
        "users": User.objects.count(),
        "chats": Chat.objects.count(),
        "messages": Message.objects.count(),
        "documents": DocumentReference.objects.count(),
    })