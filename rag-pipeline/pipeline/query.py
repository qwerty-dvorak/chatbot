"""Query enhancement strategies for the RAG pipeline."""

import json
import logging
import time
import urllib.error
import urllib.request

import litellm

from .config import cfg

logger = logging.getLogger(__name__)


def _chat(system: str, user: str) -> str:
    """Call the default chat model and return the non-streaming response text."""
    model = cfg.chat_model
    for prefix in ("openai/", "azure/", "bedrock/", "vertex_ai/"):
        if model.startswith(prefix):
            model = model.removeprefix(prefix)
            break

    url = f"{cfg.chat_base_url.rstrip('/')}/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "opencode/1.0"}
    if cfg.chat_api_key:
        headers["Authorization"] = f"Bearer {cfg.chat_api_key}"

    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"_chat HTTP {exc.code}: {exc.read().decode()}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"_chat connection error: {exc}") from exc

    duration = round(time.time() - t0, 4)
    choice = data["choices"][0]
    content = choice["message"].get("content", "") or ""
    usage = data.get("usage", {})
    logger.info(
        "[TIMING] _chat %.3fs %d+%d tokens model=%s",
        duration,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        cfg.chat_model,
    )
    return content


def _chatbot_chat(system: str, user: str) -> str:
    """Call chatbot-service's LLM endpoint, falling back to the default chat endpoint."""
    if not cfg.chatbot_llm_base_url:
        return _chat(system, user)

    model = cfg.chatbot_llm_model or cfg.chat_model
    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        api_base=cfg.chatbot_llm_base_url,
        api_key=cfg.chatbot_llm_api_key or "none",
        stream=False,
    )
    return response.choices[0].message.content or ""


def _get_chat_fn(use_chatbot_llm: bool):
    return _chatbot_chat if use_chatbot_llm else _chat


def _parse_bulleted_lines(raw: str) -> list[str]:
    items: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-"):
            item = stripped.lstrip("-").strip()
        elif stripped[0].isdigit():
            item = stripped.lstrip("0123456789").lstrip(".):- ").strip()
        else:
            item = ""
        if item:
            items.append(item)
    return items


def hyde(
    query: str,
    n: int | None = None,
    use_chatbot_llm: bool = False,
) -> list[str]:
    """Generate hypothetical answer passages for HyDE retrieval."""
    if n is None:
        n = cfg.hyde_n_documents
    chat = _get_chat_fn(use_chatbot_llm)
    system = (
        f"You are a helpful assistant that generates hypothetical documents. "
        f"Given a query, write exactly {n} short, realistic passages (2-4 "
        f"sentences each) that would directly answer or address the query. "
        f"Each passage should use different wording or cover different aspects "
        f"of the query. Return each passage on its own line prefixed with '-'. "
        f"Write only the passages - no preamble, no explanation."
    )
    return _parse_bulleted_lines(chat(system, f"Query: {query}"))[:n]


def sub_queries(
    query: str,
    n: int | None = None,
    use_chatbot_llm: bool = False,
) -> list[str]:
    """Decompose a complex query into simpler sub-queries."""
    if n is None:
        n = cfg.sub_queries_count
    chat = _get_chat_fn(use_chatbot_llm)
    system = (
        f"You are an expert at breaking down complex questions into simpler "
        f"sub-questions. If the query is simple, return the original query "
        f"as-is. If it is complex, break it into up to {n} focused "
        f"sub-questions that together cover all aspects of the original "
        f"question. Return each sub-question on its own line, prefixed with "
        f"'-'. Output only the sub-questions, nothing else."
    )
    results = _parse_bulleted_lines(chat(system, f"Query: {query}"))
    if query not in results:
        results.append(query)
    return results or [query]


def stepback(query: str, use_chatbot_llm: bool = False) -> str:
    """Generate a broader stepback question for foundational retrieval."""
    chat = _get_chat_fn(use_chatbot_llm)
    system = (
        "You are an expert at the stepback prompting technique. "
        "Given a highly specific question, abstract it into a broader, "
        "high-level question about the fundamental concepts, principles, "
        "rules, or constraints that govern the topic. "
        "Return only the stepback question, nothing else."
    )
    return chat(system, f"Query: {query}").strip()


def hypothetical_questions_for_chunk(chunk_text: str, n: int | None = None) -> list[str]:
    """Generate N index-time hypothetical questions that a chunk would answer."""
    if n is None:
        n = cfg.hypothetical_questions_per_chunk

    system = (
        f"You are an expert at generating questions from text. "
        f"Given a passage, generate exactly {n} distinct questions that the "
        f"passage directly answers. Return each question on its own line "
        f"prefixed with '-'. Output only the questions, nothing else."
    )
    return _parse_bulleted_lines(_chat(system, f"Passage:\n{chunk_text}"))[:n]


def enhance_query(
    query: str,
    enhancements: str | list[str] | tuple[str, ...] | None = None,
    use_chatbot_llm: bool = False,
) -> list[str]:
    """Apply query enhancements and return deduplicated retrieval queries."""
    configured = cfg.query_enhancements if enhancements is None else enhancements
    if isinstance(configured, str):
        enabled = {item.strip() for item in configured.split(",") if item.strip()}
    else:
        enabled = {item.strip() for item in configured if item.strip()}

    collected: list[str] = []
    if "hyde" in enabled:
        collected.extend(hyde(query, use_chatbot_llm=use_chatbot_llm))
        collected.append(query)
    if "sub_queries" in enabled:
        collected.extend(sub_queries(query, use_chatbot_llm=use_chatbot_llm))
    if "stepback" in enabled:
        stepback_query = stepback(query, use_chatbot_llm=use_chatbot_llm)
        if stepback_query:
            collected.append(stepback_query)
        collected.append(query)

    if not collected:
        return [query]

    seen: set[str] = set()
    result: list[str] = []
    for item in collected:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result or [query]
