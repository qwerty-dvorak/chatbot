import base64
import json
import logging
import time

from django.conf import settings
from django.core.files.storage import default_storage

from apps.llm.prompts import (
    COMPACTION_CONTEXT_PROMPT,
    MEMORY_CONTEXT_PROMPT,
    RAG_CONTEXT_PROMPT,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 20 * 1024 * 1024


def _build_multimodal_content(text: str, attachments: list[dict]) -> str | list[dict]:
    if not attachments:
        return text

    content_parts = []
    if text:
        content_parts.append({"type": "text", "text": text})

    for att in attachments:
        mime = att.get("mime_type", "")
        file_path = att.get("file", "")

        if mime.startswith("image/"):
            try:
                if not default_storage.exists(file_path):
                    logger.warning("Image file not found: %s", file_path)
                    content_parts.append({"type": "text", "text": f"[Image not available: {att.get('original_filename', 'unknown')}]"})
                    continue
                with default_storage.open(file_path, "rb") as f:
                    data = f.read()
                if len(data) > MAX_IMAGE_SIZE:
                    logger.warning("Image too large (%d bytes), skipping: %s", len(data), file_path)
                    content_parts.append({"type": "text", "text": f"[Image too large: {att.get('original_filename', 'unknown')}]"})
                    continue
                b64 = base64.b64encode(data).decode("ascii")
                content_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            except Exception:
                logger.exception("Failed to encode image: %s", file_path)
                content_parts.append({"type": "text", "text": f"[Image error: {att.get('original_filename', 'unknown')}]"})
        elif mime.startswith("text/"):
            try:
                if not default_storage.exists(file_path):
                    content_parts.append({"type": "text", "text": f"[File not available: {att.get('original_filename', 'unknown')}]"})
                    continue
                with default_storage.open(file_path, "r") as f:
                    file_text = f.read()
                content_parts.append({"type": "text", "text": f"--- {att.get('original_filename', 'file')} ---\n{file_text}\n--- end ---"})
            except Exception:
                logger.exception("Failed to read text file: %s", file_path)
                content_parts.append({"type": "text", "text": f"[File error: {att.get('original_filename', 'unknown')}]"})
        else:
            content_parts.append({"type": "text", "text": f"[Attachment: {att.get('original_filename', 'unknown')} ({mime})]"})

    if not content_parts:
        return text
    if len(content_parts) == 1 and content_parts[0]["type"] == "text":
        return content_parts[0]["text"]
    return content_parts


def _enable_thinking(content: str | list[dict]) -> str | list[dict]:
    prefix = "<|think|> "
    if isinstance(content, str):
        return prefix + content

    content = [dict(part) for part in content]
    for part in content:
        if part.get("type") == "text":
            part["text"] = prefix + part.get("text", "")
            return content
    content.insert(0, {"type": "text", "text": prefix.rstrip()})
    return content


class ContextBuilder:
    def __init__(self, chat, user, user_message=None):
        self.chat = chat
        self.user = user
        self.user_message = user_message
        self.messages = []
        self.total_tokens = 0

    def build(self, user_message_text: str, user_message: object | None = None) -> tuple[list[dict], dict | None]:
        self.user_message = user_message or self.user_message
        system_content = SYSTEM_PROMPT
        compaction_context = self._load_compaction()
        if compaction_context:
            system_content += f"\n\n{compaction_context}"
        rag_context, rag_log = self._search_rag(user_message_text)
        if rag_context:
            system_content += f"\n\n{RAG_CONTEXT_PROMPT.format(results=rag_context)}"
        memories = self._load_memories()
        if memories:
            system_content += f"\n\n{MEMORY_CONTEXT_PROMPT.format(memories=memories)}"
        self.messages = [{"role": "system", "content": system_content}]
        self._add_recent_chat_history()
        attachments = list(self.user_message.attachments) if self.user_message and self.user_message.attachments else []
        content = _build_multimodal_content(user_message_text, attachments)
        if self.user_message and self.user_message.metadata.get("thinking_mode"):
            content = _enable_thinking(content)
        self.messages.append({"role": "user", "content": content})
        return self.messages, rag_log

    def _decide_enhancements(self, query: str) -> dict:
        """Ask the LLM which retrieval enhancements to enable for this query."""
        start = time.time()
        try:
            from apps.llm.clients import LiteLLMClient
            client = LiteLLMClient()
            prompt = (
                "Given the user query below, decide whether each enhancement "
                "would help retrieve better RAG context. Respond ONLY with a "
                "JSON object containing three booleans: hyde, sub_queries, stepback.\n\n"
                "Guidelines:\n"
                "- hyde (Hypothetical Document Embedding): True if generating a "
                "hypothetical answer/paragraph first would help. Use for open-ended "
                "or abstract questions (e.g. \"explain X\", \"tell me about Y\"). "
                "False for simple factual lookups.\n"
                "- sub_queries: True if the question is complex or multi-part and "
                "could benefit from decomposition. False for short/factual queries.\n"
                "- stepback: True if answering requires broader context or background "
                "knowledge. False for direct, specific queries.\n\n"
                f"Query: {query}"
            )
            response = client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.1,
            )
            content = response.get("content", "").strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1]
                if "```" in content:
                    content = content.split("```")[0]
            result = json.loads(content)
            duration = time.time() - start
            logger.info("[TIMING] enhance_decide=%.3fs query_len=%d decision=%s", duration, len(query), result)
            return result
        except Exception:
            duration = time.time() - start
            logger.warning("[TIMING] enhance_decide=%.3fs FAILED, using defaults", duration)
            return {}

    @staticmethod
    def _resolve_mentions(query: str, user) -> tuple[str, list[str]]:
        import re
        from apps.documents.models import DocumentReference
        mentions = re.findall(r'@(\S+)', query)
        if not mentions:
            return query, []
        clean = re.sub(r'@\S+', '', query).strip()
        doc_refs = DocumentReference.objects.filter(
            owner=user, kind=DocumentReference.Kind.KNOWLEDGE,
        )
        sources = []
        for mention in mentions:
            lower = mention.lower()
            for ref in doc_refs:
                if lower in ref.title.lower():
                    sources.append(ref.title)
                    break
        return clean or query, sources

    def _search_rag(self, query: str) -> tuple[str | None, dict | None]:
        start = time.time()
        if not query or not getattr(settings, "RAG_ENABLED", False):
            return None, None
        try:
            from apps.knowledge.rag_client import rag_client
            if not rag_client.is_enabled():
                return None, None
            clean_query, mentioned_sources = self._resolve_mentions(query, self.user)
            if not clean_query and mentioned_sources:
                clean_query = mentioned_sources[0]
            enhance_start = time.time()
            enhancements = self._decide_enhancements(clean_query)
            enhance_time = time.time() - enhance_start
            rag_response = rag_client.search(
                clean_query, top_k=5,
                hyde=enhancements.get("hyde"),
                sub_queries=enhancements.get("sub_queries"),
                stepback=enhancements.get("stepback"),
                artifact_sources=mentioned_sources or None,
            )
            results = rag_response.get("results", [])
            enhanced_queries = rag_response.get("enhanced_queries", [])
            total_time = time.time() - start
            logger.info("[TIMING] rag_search=%.3fs results=%d", total_time, len(results if results else []))
            search_timing = rag_response.get("timing") or {}
            mode = "hybrid"
            if enhancements.get("hyde"):
                mode = "hyde"
            if enhancements.get("sub_queries"):
                mode = "sub_queries"
            if enhancements.get("stepback"):
                mode = "stepback"
            rag_log = {
                "retrieval_mode": mode,
                "use_reranker": True,
                "hierarchical": False,
                "enhancements": enhancements,
                "enhanced_queries": enhanced_queries,
                "total_results": rag_response.get("total", len(results)),
                "result_sources": list({r.get("source", "").split("/")[-1] for r in results if r.get("source")}),
                "mentioned_sources": mentioned_sources,
                "rag_used": True,
                "step_timing": search_timing,
                "timing": {
                    "enhance_query": round(enhance_time, 3),
                    "total": round(total_time, 3),
                    **{k: round(v, 3) for k, v in search_timing.items() if isinstance(v, (int, float)) and k != "total"},
                },
            }
            if not results:
                return None, rag_log
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
            return ("\n\n".join(lines) if lines else None), rag_log
        except Exception:
            total_time = time.time() - start
            logger.exception("[TIMING] rag_search=%.3fs FAILED", total_time)
            return None, {"rag_used": False, "error": "RAG search failed", "total_results": 0, "timing": {"total": round(total_time, 3)}}

    def _add_recent_chat_history(self):
        from apps.compaction.services import messages_after_compaction

        recent = messages_after_compaction(self.chat)
        if self.user_message:
            recent = [message for message in recent if message.id != self.user_message.id]

        for msg in recent[-20:]:
            attachments = list(msg.attachments) if msg.attachments else []
            content = _build_multimodal_content(msg.content, attachments)
            self.messages.append({
                "role": msg.role,
                "content": content,
            })

    def _load_compaction(self) -> str | None:
        from apps.compaction.services import latest_compaction

        compaction = latest_compaction(self.chat)
        if not compaction:
            return None
        facts = "\n".join(f"- {fact}" for fact in compaction.facts) or "None"
        open_questions = (
            "\n".join(f"- {question}" for question in compaction.open_questions) or "None"
        )
        return (
            COMPACTION_CONTEXT_PROMPT.format(summary=compaction.summary, facts=facts)
            + f"\n\nOpen questions from earlier context:\n{open_questions}"
        )

    def _load_memories(self) -> str | None:
        """Load user memories and format them for context injection."""
        if not self.user or not self.user.is_authenticated:
            return None
        try:
            from apps.memory.services import get_user_memories
            from apps.memory.models import MemorySettings
            settings_obj = MemorySettings.objects.filter(user=self.user).first()
            if settings_obj and not settings_obj.is_enabled:
                return None
            max_memories = min(settings_obj.max_tokens // 50 if settings_obj else 5, 20)
            memories = get_user_memories(self.user, top_k=max_memories)
            if not memories:
                return None
            lines = [f"- {m.content}" for m in memories]
            return "\n".join(lines)
        except Exception:
            logger.debug("Failed to load memories for context")
            return None

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4
