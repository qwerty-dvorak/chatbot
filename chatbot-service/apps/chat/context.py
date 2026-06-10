import logging

from django.conf import settings

from apps.llm.prompts import RAG_CONTEXT_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ContextBuilder:
    def __init__(self, chat, user):
        self.chat = chat
        self.user = user
        self.messages = []
        self.total_tokens = 0

    def build(self, user_message_text: str) -> list[dict[str, str]]:
        system_content = SYSTEM_PROMPT
        rag_context = self._search_rag(user_message_text)
        if rag_context:
            system_content += f"\n\n{RAG_CONTEXT_PROMPT.format(results=rag_context)}"
        self.messages = [{"role": "system", "content": system_content}]
        self._add_recent_chat_history()
        self.messages.append({"role": "user", "content": user_message_text})
        return self.messages

    def _search_rag(self, query: str) -> str | None:
        if not query or not getattr(settings, "RAG_ENABLED", False):
            return None
        try:
            from apps.knowledge.rag_client import rag_client
            if rag_client.is_enabled():
                results = rag_client.search(query, top_k=5, tier=None)
            else:
                results = self._local_search(query)
            if not results:
                return None
            lines = []
            for r in results:
                source = r.get("source", "")
                text = r.get("text", "").strip()
                meta = r.get("metadata", {}) or {}
                filename = meta.get("filename", "") or source.split("/")[-1]
                if filename:
                    score = r.get("score", 0)
                    lines.append(f"[{filename}] (relevance: {score:.2f}) {text[:500]}")
                else:
                    lines.append(text[:500])
            return "\n\n".join(lines) if lines else None
        except Exception:
            logger.exception("RAG search failed")
            return None

    def _local_search(self, query: str) -> list[dict]:
        from apps.knowledge.models import DocumentChunk
        qs = DocumentChunk.objects.select_related("document").filter(
            document__owner=self.user,
            content__icontains=query,
        )[:5]
        results = []
        for c in qs:
            results.append({
                "text": c.content,
                "score": 1.0,
                "source": c.document.title,
                "metadata": {"filename": c.document.original_filename or c.document.title},
            })
        return results

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
