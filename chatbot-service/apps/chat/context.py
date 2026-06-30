import base64
import json
import logging
import re
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.files.storage import default_storage

from apps.chat.models import Message
from apps.compaction.services import latest_compaction, messages_after_compaction
from apps.documents.models import DocumentReference
from apps.knowledge.models import KnowledgeDocument
from apps.knowledge.rag_client import rag_client
from apps.llm.clients import ChatClient
from apps.llm.prompts import (
    COMPACTION_CONTEXT_PROMPT,
    DOCUMENT_SELECTION_CONTEXT_PROMPT,
    MEMORY_CONTEXT_PROMPT,
    RAG_CONTEXT_PROMPT,
    RETRIEVAL_ROUTER_PROMPT,
    SYSTEM_PROMPT,
)
from apps.memory.models import MemorySettings
from apps.memory.services import get_user_memories
from apps.tools.builtin import _ingest_from_attachment

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 20 * 1024 * 1024
MAX_INLINE_TEXT_SIZE = 10 * 1024


@dataclass(frozen=True)
class DocumentMentionResolution:
    """Resolved document selectors and the query text left for retrieval."""

    clean_query: str
    selected_sources: tuple[str, ...] = ()
    unresolved_mentions: tuple[str, ...] = ()
    had_mentions: bool = False
    selection_origin: str = "none"


def _parse_json_object(content: str) -> dict:
    """Parse one JSON object, tolerating markdown fences or leading text."""
    value = (content or "").strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1]
        if "```" in value:
            value = value.split("```", 1)[0]
        value = value.strip()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        if start < 0:
            raise
        parsed, _ = json.JSONDecoder().raw_decode(value[start:])

    if not isinstance(parsed, dict):
        raise ValueError("LLM routing decision must be a JSON object")  # noqa: TRY004, TRY003, EM101
    return parsed


def _normalise_rag_decision(content: str, fallback_query: str) -> dict:
    """Validate the LLM's retrieval decision instead of trusting truthy values."""
    raw = _parse_json_object(content)
    if not isinstance(raw.get("use_rag"), bool):
        raise ValueError("LLM routing decision must contain boolean use_rag")  # noqa: TRY004, TRY003, EM101

    query = raw.get("search_query", "")
    if not isinstance(query, str) or not query.strip():
        query = fallback_query

    decision = {
        "use_rag": raw["use_rag"],
        "search_query": query.strip(),
    }
    for key, default in (
        ("hyde", False),
        ("sub_queries", False),
        ("stepback", False),
        ("use_reranker", True),
    ):
        value = raw.get(key, default)
        decision[key] = value if isinstance(value, bool) else default
    return decision


def resolve_document_mentions(query: str, user) -> DocumentMentionResolution:
    """
    Resolve @mentions against the user's document titles.

    Known titles are matched longest-first, which supports filenames containing
    spaces. Unknown selectors are removed from the retrieval query but retained
    as unresolved metadata so they cannot accidentally broaden search scope.
    """
    mention_marker = re.compile(r"(?<![\w@])@")
    if not query or not mention_marker.search(query):
        return DocumentMentionResolution(clean_query=query)

    references = []
    if user and getattr(user, "is_authenticated", False):
        references = list(
            DocumentReference.objects.filter(
                owner=user,
                kind=DocumentReference.Kind.KNOWLEDGE,
            ).only("title")
        )

    spans: list[tuple[int, int]] = []
    selected: list[str] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < used_end and end > used_start for used_start, used_end in occupied)

    for ref in sorted(references, key=lambda item: len(item.title), reverse=True):
        pattern = re.compile(
            rf"(?<![\w@])@{re.escape(ref.title)}(?=$|[\s,;:!?()\[\]{{}}])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(query):
            if overlaps(match.start(), match.end()):
                continue
            spans.append(match.span())
            occupied.append(match.span())
            if ref.title not in selected:
                selected.append(ref.title)

    unresolved: list[str] = []
    token_pattern = re.compile(r"(?<![\w@])@([^\s,;:!?()\[\]{}]+)")
    for match in token_pattern.finditer(query):
        if overlaps(match.start(), match.end()):
            continue
        token = match.group(1)
        candidates = [
            ref.title for ref in references
            if ref.title.lower().startswith(token.lower())
        ]
        if len(candidates) == 1:
            if candidates[0] not in selected:
                selected.append(candidates[0])
        elif token not in unresolved:
            unresolved.append(token)
        spans.append(match.span())
        occupied.append(match.span())

    clean_parts = list(query)
    for start, end in spans:
        clean_parts[start:end] = " " * (end - start)
    clean_query = " ".join("".join(clean_parts).split())
    if not clean_query and selected:
        clean_query = f"Summarize the requested document(s): {', '.join(selected)}"

    return DocumentMentionResolution(
        clean_query=clean_query,
        selected_sources=tuple(selected),
        unresolved_mentions=tuple(unresolved),
        had_mentions=True,
        selection_origin="explicit",
    )


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
                size = att.get("size_bytes", 0)
                if size > MAX_INLINE_TEXT_SIZE:
                    placeholder = f"[Large file: {att.get('original_filename', 'file')} ({size//1024} KB)]"
                    content_parts.append({"type": "text", "text": placeholder})
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

    def _merge_attachment_sources(
        self,
        mentions: DocumentMentionResolution,
        attachments: list[dict],
    ) -> DocumentMentionResolution:
        """Add attachment filenames as implicit document sources."""
        if not attachments:
            return mentions
        filenames = tuple(
            att.get("original_filename", "")
            for att in attachments
            if att.get("original_filename")
        )
        existing = set(mentions.selected_sources)
        new_sources = tuple(f for f in filenames if f not in existing)
        if not new_sources:
            return mentions
        return DocumentMentionResolution(
            clean_query=mentions.clean_query,
            selected_sources=mentions.selected_sources + new_sources,
            unresolved_mentions=mentions.unresolved_mentions,
            had_mentions=True,
            selection_origin=mentions.selection_origin if mentions.had_mentions else "explicit",
        )

    def build(self, user_message_text: str, user_message: object | None = None) -> tuple[list[dict], dict | None]:
        self.user_message = user_message or self.user_message
        # Resolve selectors, make the routing decision, and retrieve before
        # assembling any optional conversation context for the answer model.
        mentions = resolve_document_mentions(user_message_text, self.user)
        mentions = self._inherit_document_selection(mentions)
        # Inject attachment filenames as implicit document sources
        attachments = list(self.user_message.attachments) if self.user_message and self.user_message.attachments else []
        mentions = self._merge_attachment_sources(mentions, attachments)
        rag_context, rag_log = self._search_rag(mentions)

        system_content = SYSTEM_PROMPT
        compaction_context = self._load_compaction()
        if compaction_context:
            system_content += f"\n\n{compaction_context}"
        if mentions.had_mentions or mentions.selected_sources:
            selection_lines = []
            if mentions.selected_sources:
                selection_lines.append(
                    "Requested documents"
                    + (" (continued from the previous turn)" if mentions.selection_origin == "inherited" else "")
                    + ": "
                    + ", ".join(mentions.selected_sources)
                )
            if mentions.unresolved_mentions:
                selection_lines.append(
                    "Unresolved document mentions: "
                    + ", ".join(f"@{name}" for name in mentions.unresolved_mentions)
                )
            system_content += "\n\n" + DOCUMENT_SELECTION_CONTEXT_PROMPT.format(
                selection="\n".join(selection_lines) or "No accessible documents were resolved."
            )
        if rag_context:
            system_content += f"\n\n{RAG_CONTEXT_PROMPT.format(results=rag_context)}"
        memories = self._load_memories()
        if memories:
            system_content += f"\n\n{MEMORY_CONTEXT_PROMPT.format(memories=memories)}"
        self.messages = [{"role": "system", "content": system_content}]
        self._add_recent_chat_history()
        content = _build_multimodal_content(user_message_text, attachments)
        if self.user_message and self.user_message.metadata.get("thinking_mode"):
            content = _enable_thinking(content)
        self.messages.append({"role": "user", "content": content})
        return self.messages, rag_log

    def _inherit_document_selection(
        self,
        mentions: DocumentMentionResolution,
    ) -> DocumentMentionResolution:
        """
        Carry an explicit document selection into follow-up turns.

        An explicit selector on the current turn always wins, including an
        unresolved selector (which must not silently fall back to an older
        document). Otherwise the latest logged document scope remains active.
        """
        if mentions.had_mentions or mentions.selected_sources:
            return mentions


        recent = Message.objects.filter(
            chat=self.chat,
            role=Message.Role.USER,
        )
        if self.user_message:
            recent = recent.exclude(id=self.user_message.id)

        for message in recent.order_by("-created_at")[:20]:
            rag_log = (message.metadata or {}).get("rag_search_log") or {}
            sources = rag_log.get("mentioned_sources") or []
            if rag_log.get("selection_origin") == "explicit" and not sources:
                return mentions
            if not sources:
                continue
            accessible = set(
                DocumentReference.objects.filter(
                    owner=self.user,
                    kind=DocumentReference.Kind.KNOWLEDGE,
                    title__in=sources,
                ).values_list("title", flat=True)
            )
            selected = tuple(source for source in sources if source in accessible)
            if selected:
                return DocumentMentionResolution(
                    clean_query=mentions.clean_query,
                    selected_sources=selected,
                    selection_origin="inherited",
                )
        return mentions

    def _decide_retrieval(self, mentions: DocumentMentionResolution) -> dict:
        """Ask the LLM whether RAG is needed and how to run it, in one call."""
        start = time.time()
        query = mentions.clean_query
        fallback_query = query or (
            f"Summarize the requested document(s): {', '.join(mentions.selected_sources)}"
        )
        try:
            client = ChatClient()
            prompt = RETRIEVAL_ROUTER_PROMPT.format(
                query=json.dumps(query),
                sources=json.dumps(list(mentions.selected_sources)),
                unresolved=json.dumps(list(mentions.unresolved_mentions)),
            )
            response = client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=220,
                temperature=0.1,
            )
            result = _normalise_rag_decision(response.get("content", ""), fallback_query)
            duration = time.time() - start
            result["decision_source"] = "llm"
            logger.info(
                "[TIMING] rag_decide=%.3fs query_len=%d decision=%s",
                duration,
                len(query),
                result,
            )
            return result  # noqa: TRY300
        except Exception:
            duration = time.time() - start
            logger.exception("[TIMING] rag_decide=%.3fs FAILED, using safe fallback", duration)
            return {
                # Explicit, resolved document requests should still work during a
                # transient router-format failure. Unscoped queries do not trigger
                # speculative retrieval in that case.
                "use_rag": bool(mentions.selected_sources),
                "search_query": fallback_query,
                "hyde": False,
                "sub_queries": False,
                "stepback": False,
                "use_reranker": True,
                "decision_source": "fallback",
            }

    @staticmethod
    def _resolve_mentions(query: str, user) -> tuple[str, list[str]]:
        """Compatibility wrapper for callers using the old tuple interface."""
        resolution = resolve_document_mentions(query, user)
        return resolution.clean_query, list(resolution.selected_sources)

    def _search_rag(
        self,
        mentions: DocumentMentionResolution,
    ) -> tuple[str | None, dict | None]:
        start = time.time()
        if not (mentions.clean_query or mentions.had_mentions):
            return None, None
        if not getattr(settings, "RAG_ENABLED", False):
            return None, None
        try:
            rag_api_available = rag_client.is_enabled()

            # Auto-ingest mentioned documents not yet ready (needs RAG API).
            # Skip auto-ingest when the RAG pipeline isn't running — the
            # direct-text fallback below will still work for already-ingested docs.
            if rag_api_available and mentions.selected_sources:
                for source in list(mentions.selected_sources):
                    doc_ready = DocumentReference.objects.filter(
                        owner=self.user,
                        title__iexact=source,
                        artifact_revision__processing_status="ready",
                    ).exists()
                    if not doc_ready:
                        ingested = _ingest_from_attachment(self.chat, source, self.user)
                        if ingested:
                            logger.info("Auto-ingested '%s' for RAG mention", source)

            # For explicitly @mentioned documents inject extracted text directly
            # from the DB — no LLM routing or vector search needed.
            # Prefer KnowledgeDocument (RAG pipeline), fall back to
            # ArtifactRevision.extracted_text (local pipeline).
            if mentions.selected_sources:
                direct_lines = []
                for source in mentions.selected_sources:
                    doc_ref = DocumentReference.objects.filter(
                        owner=self.user,
                        title__iexact=source,
                        artifact_revision__processing_status="ready",
                    ).select_related("artifact_revision__blob").first()
                    if not doc_ref or not doc_ref.artifact_revision or not doc_ref.artifact_revision.blob:
                        continue
                    text = ""
                    rag_doc = KnowledgeDocument.objects.filter(
                        sha256=doc_ref.artifact_revision.blob.content_hash,
                        status__in=("ready", "completed", "succeeded"),
                    ).order_by("-created_at").first()
                    if rag_doc:
                        text = rag_doc.extracted_text or rag_doc.analysis_summary or ""
                    if not text:
                        text = doc_ref.artifact_revision.extracted_text or ""
                    if text:
                        direct_lines.append(f"[{source}]\n{text[:3000]}")
                if direct_lines:
                    total_time = time.time() - start
                    return "\n\n".join(direct_lines), {
                        "rag_used": True,
                        "rag_needed": True,
                        "retrieval_mode": "direct",
                        "total_results": len(direct_lines),
                        "result_sources": list(mentions.selected_sources),
                        "mentioned_sources": list(mentions.selected_sources),
                        "selection_origin": mentions.selection_origin,
                        "search_query": mentions.clean_query,
                        "timing": {"total": round(total_time, 3)},
                    }

            # A selector was supplied but did not resolve.
            if mentions.had_mentions and not mentions.selected_sources:
                return None, {
                    "rag_used": False, "retrieval_mode": "blocked_unresolved_mentions",
                    "error": "No requested document mention could be resolved",
                    "mentioned_sources": [], "unresolved_mentions": list(mentions.unresolved_mentions),
                    "timing": {"total": round(time.time() - start, 3)},
                }

            # Vector search requires RAG API — nothing more to do without it.
            if not rag_api_available:
                return None, None

            # No @mentions → LLM routing → vector search
            if not mentions.selected_sources and not mentions.unresolved_mentions:
                decision = {
                    "use_rag": False, "search_query": mentions.clean_query,
                    "hyde": False, "sub_queries": False, "stepback": False,
                    "use_reranker": True, "decision_source": "fast-path",
                }
                decision_time = 0
            else:
                decision_start = time.time()
                decision = self._decide_retrieval(mentions)
                decision_time = time.time() - decision_start

            if not decision["use_rag"]:
                return None, {
                    "rag_used": False, "rag_needed": False,
                    "retrieval_mode": "not_needed",
                    "decision_source": decision["decision_source"],
                    "search_query": decision["search_query"],
                    "mentioned_sources": list(mentions.selected_sources),
                    "unresolved_mentions": list(mentions.unresolved_mentions),
                    "selection_origin": mentions.selection_origin,
                    "enhancements": {key: decision[key] for key in ("hyde", "sub_queries", "stepback")},
                    "use_reranker": decision["use_reranker"],
                    "timing": {"decision": round(decision_time, 3), "total": round(time.time() - start, 3)},
                }

            rag_response = rag_client.search(
                decision["search_query"],
                top_k=getattr(settings, "RAG_TOP_K", 5),
                use_reranker=decision["use_reranker"],
                hyde=decision["hyde"],
                sub_queries=decision["sub_queries"],
                stepback=decision["stepback"],
                artifact_sources=list(mentions.selected_sources) or None,
            )
            results = rag_response.get("results", [])
            enhanced_queries = rag_response.get("enhanced_queries", [])
            total_time = time.time() - start
            logger.info("[TIMING] rag_search=%.3fs results=%d", total_time, len(results if results else []))
            search_timing = rag_response.get("timing") or {}
            enabled_enhancements = [
                key for key in ("hyde", "sub_queries", "stepback") if decision[key]
            ]
            rag_log = {
                "retrieval_mode": "+".join(enabled_enhancements) or "hybrid",
                "hierarchical": rag_response.get("hierarchical", False),
                "enhanced_queries": enhanced_queries,
                "total_results": rag_response.get("total", len(results)),
                "result_sources": sorted({
                    r.get("source", "").split("/")[-1]
                    for r in results if r.get("source")
                }),
                "rag_used": True,
                "rag_needed": True,
                "decision_source": decision["decision_source"],
                "search_query": decision["search_query"],
                "mentioned_sources": list(mentions.selected_sources),
                "unresolved_mentions": list(mentions.unresolved_mentions),
                "selection_origin": mentions.selection_origin,
                "enhancements": {key: decision[key] for key in ("hyde", "sub_queries", "stepback")},
                "use_reranker": decision["use_reranker"],
                "step_timing": search_timing,
                "timing": {
                    "decision": round(decision_time, 3),
                    "total": round(total_time, 3),
                    **{
                        key: round(value, 3)
                        for key, value in search_timing.items()
                        if isinstance(value, (int, float)) and key != "total"
                    },
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
                method = r.get("method", "")
                if method == "hyde_fallback":
                    lines.append(f"[Related context] {text[:500]}")
                elif filename and filename != "__hyde_fallback__":
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
