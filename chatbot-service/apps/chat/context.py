from django.conf import settings

from apps.llm.prompts import SYSTEM_PROMPT


class ContextBuilder:
    def __init__(self, chat, user):
        self.chat = chat
        self.user = user
        self.messages = []
        self.total_tokens = 0

    def build(self, user_message_text: str) -> list[dict[str, str]]:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._add_recent_chat_history()
        self.messages.append({"role": "user", "content": user_message_text})
        return self.messages

    def _add_recent_chat_history(self):
        from .models import Message

        recent = Message.objects.filter(
            chat=self.chat, status=Message.Status.COMPLETED
        ).exclude(role=Message.Role.SYSTEM).order_by("-created_at")[:20]

        for msg in reversed(recent):
            self.messages.append({
                "role": msg.role,
                "content": msg.content,
            })

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4
