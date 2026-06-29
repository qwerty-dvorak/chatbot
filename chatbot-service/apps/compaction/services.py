import json
import re

from django.conf import settings

from .models import ChatCompaction

MIN_MESSAGES_TO_COMPACT = 10
RECENT_MESSAGES_TO_KEEP = 6


def estimate_tokens(value) -> int:
    if not isinstance(value, str):
        value = json.dumps(value, default=str)
    return max(1, len(value) // 4) if value else 0


def latest_compaction(chat):
    return ChatCompaction.objects.filter(chat=chat).order_by("-created_at").first()


def messages_after_compaction(chat):
    from apps.chat.models import Message

    messages = list(
        Message.objects.filter(
            chat=chat,
            status=Message.Status.COMPLETED,
        )
        .exclude(role=Message.Role.SYSTEM)
        .order_by("created_at")
    )
    previous = latest_compaction(chat)
    if not previous:
        return messages

    for index, message in enumerate(messages):
        if message.id == previous.to_message_id:
            return messages[index + 1 :]
    return messages


def _clean_json_response(content: str) -> dict:
    content = content.strip()
    if "<|channel" in content:
        from apps.llm.streaming import ChannelContentParser

        parser = ChannelContentParser()
        parts = parser.feed(content) + parser.finish()
        final_text = "".join(text for kind, text in parts if kind == "text").strip()
        if final_text:
            content = final_text
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fenced:
        content = fenced.group(1).strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"summary": content, "facts": [], "open_questions": []}


def context_usage(chat) -> dict:
    from apps.llm.model_info import get_model_context_limit
    from apps.llm.prompts import COMPACTION_CONTEXT_PROMPT, SYSTEM_PROMPT

    previous = latest_compaction(chat)
    context_parts = [SYSTEM_PROMPT]
    if previous:
        context_parts.append(
            COMPACTION_CONTEXT_PROMPT.format(
                summary=previous.summary,
                facts="\n".join(f"- {fact}" for fact in previous.facts) or "None",
            )
        )
    recent = messages_after_compaction(chat)[-20:]
    context_parts.extend(f"{message.role}: {message.content}" for message in recent)
    used_tokens = estimate_tokens("\n\n".join(context_parts))
    model_info = get_model_context_limit()
    max_tokens = model_info["max_model_len"]
    return {
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
        "percentage": min(100, round((used_tokens / max_tokens) * 100, 1)),
        "model": model_info["model"],
        "source": model_info["source"],
        "compacted": bool(previous),
    }


def compact_chat(chat):
    messages = messages_after_compaction(chat)
    if len(messages) < MIN_MESSAGES_TO_COMPACT:
        return None

    selected = messages[:-RECENT_MESSAGES_TO_KEEP]
    if not selected:
        return None

    previous = latest_compaction(chat)
    prior_context = ""
    if previous:
        prior_context = (
            "Previous compacted context:\n"
            f"Summary: {previous.summary}\n"
            f"Facts: {json.dumps(previous.facts)}\n"
            f"Open questions: {json.dumps(previous.open_questions)}\n\n"
        )
    transcript = "\n\n".join(
        f"{message.role.upper()}: {message.content}" for message in selected
    )
    prompt = (
        "Compact this conversation for use as context in future assistant turns. "
        "Preserve decisions, user preferences, names, constraints, technical details, "
        "unfinished work, and unresolved questions. Remove repetition and small talk. "
        "Return only valid JSON with this shape: "
        '{"summary":"concise narrative","facts":["fact"],'
        '"open_questions":["question"]}.\n\n'
        f"{prior_context}Conversation to compact:\n{transcript}"
    )

    from apps.llm.clients import ChatClient

    response = ChatClient().chat_completion(
        messages=[
            {
                "role": "system",
                "content": "You are a precise conversation compaction engine.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=1800,
        temperature=0.1,
    )
    parsed = _clean_json_response(response.get("content", ""))
    summary = str(parsed.get("summary", "")).strip()
    if not summary:
        return None
    facts = parsed.get("facts", [])
    open_questions = parsed.get("open_questions", [])
    facts = facts if isinstance(facts, list) else []
    open_questions = open_questions if isinstance(open_questions, list) else []

    compaction = ChatCompaction.objects.create(
        chat=chat,
        from_message=selected[0],
        to_message=selected[-1],
        summary=summary,
        facts=[str(fact) for fact in facts],
        open_questions=[str(question) for question in open_questions],
        token_count=estimate_tokens(
            {"summary": summary, "facts": facts, "open_questions": open_questions}
        ),
        model=settings.CHAT_MODEL,
    )
    return compaction
