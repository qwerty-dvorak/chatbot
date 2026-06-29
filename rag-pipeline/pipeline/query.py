"""Query enhancement strategies for the RAG pipeline."""

import logging
import time

import httpx

from .config import cfg

logger = logging.getLogger(__name__)


def _chat(system: str, user: str) -> str:
    """Call the chat LLM with system and user messages."""
    model = cfg.chat_model
    for prefix in ("openai/", "azure/", "bedrock/", "vertex_ai/"):
        if model.startswith(prefix):
            model = model.removeprefix(prefix)
            break
    url = f"{cfg.chat_base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json", "User-Agent": "opencode/1.0"}
    if cfg.chat_api_key:
        headers["Authorization"] = f"Bearer {cfg.chat_api_key}"
    t0 = time.time()
    try:
        resp = httpx.post(
            url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        msg = f"_chat HTTP {exc.response.status_code}: {exc.response.text}"
        raise RuntimeError(msg) from exc
    except httpx.RequestError as exc:
        msg = f"_chat connection error: {exc}"
        raise RuntimeError(msg) from exc
    duration = round(time.time() - t0, 4)
    choice = data["choices"][0]
    content = choice["message"].get("content", "") or ""
    usage = data.get("usage", {})
    logger.info(
        "[TIMING] _chat %.3fs %d+%d tokens model=%s", duration, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), cfg.chat_model
    )
    return content


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


def hyde(query: str, n: int | None = None) -> list[str]:
    """Generate hypothetical documents for a query."""
    if n is None:
        n = cfg.hyde_n_documents
    system = (
        f"You are a helpful assistant that generates hypothetical documents. "
        f"Given a query, write exactly {n} short, realistic passages (2-4 "
        f"sentences each) that would directly answer or address the query. "
        f"Each passage should use different wording or cover different aspects "
        f"of the query. Return each passage on its own line prefixed with '-'. "
        f"Write only the passages - no preamble, no explanation."
    )
    return _parse_bulleted_lines(_chat(system, f"Query: {query}"))[:n]


def sub_queries(query: str, n: int | None = None) -> list[str]:
    """Decompose a complex query into sub-queries."""
    if n is None:
        n = cfg.sub_queries_count
    system = (
        f"You are an expert at breaking down complex questions into simpler "
        f"sub-questions. If the query is simple, return the original query "
        f"as-is. If it is complex, break it into up to {n} focused "
        f"sub-questions that together cover all aspects of the original "
        f"question. Return each sub-question on its own line, prefixed with "
        f"'-'. Output only the sub-questions, nothing else."
    )
    results = _parse_bulleted_lines(_chat(system, f"Query: {query}"))
    if query not in results:
        results.append(query)
    return results or [query]


def stepback(query: str) -> str:
    """Abstract a specific query into a broader stepback question."""
    system = (
        "You are an expert at the stepback prompting technique. "
        "Given a highly specific question, abstract it into a broader, "
        "high-level question about the fundamental concepts, principles, "
        "rules, or constraints that govern the topic. "
        "Return only the stepback question, nothing else."
    )
    return _chat(system, f"Query: {query}").strip()


def hypothetical_questions_for_chunk(chunk_text: str, n: int | None = None) -> list[str]:
    """Generate hypothetical questions that a chunk answers."""
    if n is None:
        n = cfg.hypothetical_questions_per_chunk
    system = (
        f"You are an expert at generating questions from text. "
        f"Given a passage, generate exactly {n} distinct questions that the "
        f"passage directly answers. Return each question on its own line "
        f"prefixed with '-'. Output only the questions, nothing else."
    )
    return _parse_bulleted_lines(_chat(system, f"Passage:\n{chunk_text}"))[:n]


def enhance_query(query: str, enhancements: str | list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Apply configured query enhancements and return deduplicated queries."""
    configured = cfg.query_enhancements if enhancements is None else enhancements
    if isinstance(configured, str):
        enabled = {item.strip() for item in configured.split(",") if item.strip()}
    else:
        enabled = {item.strip() for item in configured if item.strip()}
    collected: list[str] = []
    if "hyde" in enabled:
        collected.extend(hyde(query))
        collected.append(query)
    if "sub_queries" in enabled:
        collected.extend(sub_queries(query))
    if "stepback" in enabled:
        stepback_query = stepback(query)
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
