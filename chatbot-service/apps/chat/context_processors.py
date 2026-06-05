from .models import Chat


def sidebar(request):
    if not request.user.is_authenticated:
        return {"sidebar_chats": []}
    return {
        "sidebar_chats": Chat.objects.filter(
            user=request.user, archived=False
        ).order_by("-updated_at")[:40],
    }
