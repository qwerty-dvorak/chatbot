"""Query enhancement strategies for the RAG pipeline.

Provides four enhancement functions (HyDE, sub-queries, stepback,
hypothetical questions) and a single public ``enhance_query`` entry point
that applies whichever strategies are enabled in ``cfg.query_enhancements``.
"""

import json
import logging
import time
import urllib.error
import urllib.request

from .config import cfg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _chat(system: str, user: str) -> str:
    """Call the chat model and return the response text.

    Non-streaming.  Uses ``cfg.chat_model`` as the model name.
    """
    url = f"{cfg.chat_base_url.rstrip('/')}/chat/completions"
    body = json.dumps({
        "model": cfg.chat_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "opencode/1.0",
    }
    if cfg.chat_api_key:
        headers["Authorization"] = f"Bearer {cfg.chat_api_key}"

    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"_chat HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"_chat connection error: {e}")

    duration = round(time.time() - t0, 4)
    choice = data["choices"][0]
    content = choice["message"].get("content", "") or ""
    usage = data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    logger.info("[TIMING] _chat %.3fs %d+%d tokens model=%s",
                duration, tokens_in, tokens_out, cfg.chat_model)
    return content


# ---------------------------------------------------------------------------
# Enhancement functions
# ---------------------------------------------------------------------------

def hyde(query: str, n: int | None = None) -> list[str]:
    if n is None:
        n = cfg.hyde_n_documents

    system = (
        f"You are a helpful assistant that generates hypothetical documents. "
        f"Given a query, write exactly {n} short, realistic passages (2-4 "
        f"sentences each) that would directly answer or address the query. "
        f"Each passage should use different wording or cover different aspects "
        f"of the query. "
        f"Return each passage on its own line prefixed with '-'. "
        f"Write only the passages — no preamble, no explanation."
    )
    user = f"Query: {query}"
    raw = _chat(system, user)

    docs: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-"):
            d = stripped.lstrip("-").strip()
            if d:
                docs.append(d)
        elif stripped and stripped[0].isdigit():
            d = stripped.lstrip("0123456789").lstrip(".):- ").strip()
            if d:
                docs.append(d)

    return docs[:n] if n else docs


def sub_queries(query: str, n: int | None = None) -> list[str]:
    if n is None:
        n = cfg.sub_queries_count

    system = (
        f"You are an expert at breaking down complex questions into "
        f"simpler sub-questions.  Given a query, decide if it needs "
        f"decomposition:\n"
        f"- If the query is simple and straightforward, return the "
        f"original query as-is.\n"
        f"- If the query is complex or multi-faceted, break it into "
        f"up to {n} simpler, focused sub-questions that together "
        f"cover all aspects of the original question.\n"
        f"Return each sub-question on its own line, prefixed with '-'. "
        f"Output only the sub-questions, nothing else."
    )
    user = f"Query: {query}"
    raw = _chat(system, user)

    results: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-"):
            q = stripped.lstrip("-").strip()
            if q:
                results.append(q)
        elif stripped and stripped[0].isdigit():
            q = stripped.lstrip("0123456789").lstrip(".):- ").strip()
            if q:
                results.append(q)

    if not results:
        results.append(query)
    elif query not in results:
        results.append(query)
    return results


def stepback(query: str) -> str:
    """Generate a stepback question that abstracts the specific query into
    a broader question about fundamental concepts, principles, or rules.

    The architecture diagram describes a two-stage process:

      ② Stepback abstraction
         LLM rewrites the narrow query into a generalised stepback question
         targeting core concepts or rules.

      ③-④ Retrieval with stepback question
         The stepback question is embedded and searched against the vector
         store, surfacing chunks with foundational principles.

      ⑤-⑥ Stepback answer generation
         The retrieved chunks are fed to an LLM which generates a high-level
         *stepback answer* detailing the rules, limitations, or context
         requested by the abstract question.  (This happens in the search
         pipeline or downstream chatbot-service.)

      ⑦-⑧ Final synthesis
         Both the original question (specific) and the stepback answer
         (foundational) are routed to the final LLM for a grounded answer.

    Args:
        query: The user's original search query.

    Returns:
        A single, more abstract stepback question focusing on the underlying
        principles, rules, or domain knowledge needed to answer the query.
    """
    system = (
        "You are an expert at the stepback prompting technique. "
        "Given a highly specific question, abstract it into a broader, "
        "high-level question about the fundamental concepts, principles, "
        "rules, or constraints that govern the topic. "
        "The stepback question should target the core knowledge needed "
        "to understand and answer the original query. "
        "Return only the stepback question, nothing else."
    )
    user = f"Query: {query}"
    return _chat(system, user).strip()


def hypothetical_questions_for_chunk(chunk_text: str, n: int | None = None) -> list[str]:
    """Generate N hypothetical questions that this chunk would answer.

    Used at **index time** to augment each chunk with additional searchable
    content.  When the generated questions are indexed alongside the chunk,
    queries that match the questions will surface the chunk even if the
    query wording differs from the chunk's literal text.

    Args:
        chunk_text: The text content of the chunk.
        n:          Number of questions to generate.  Defaults to
                    ``cfg.hypothetical_questions_per_chunk``.

    Returns:
        A list of question strings (length may be less than *n* if the LLM
        returns fewer).
    """
    if n is None:
        n = cfg.hypothetical_questions_per_chunk

    system = (
        f"You are an expert at generating questions from text. "
        f"Given a passage, generate exactly {n} distinct questions that the "
        f"passage directly answers. "
        f"Return each question on its own line prefixed with '-'. "
        f"Output only the questions, nothing else."
    )
    user = f"Passage:\n{chunk_text}"
    raw = _chat(system, user)

    questions: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-"):
            q = stripped.lstrip("-").strip()
            if q:
                questions.append(q)
        elif stripped and stripped[0].isdigit():
            q = stripped.lstrip("0123456789").lstrip(".):- ").strip()
            if q:
                questions.append(q)

    return questions[:n] if n else questions


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def enhance_query(query: str, enhancements: str | list[str] | None = None) -> list[str]:
    configured = cfg.query_enhancements if enhancements is None else enhancements
    if isinstance(configured, str):
        enabled = {s.strip() for s in configured.split(",") if s.strip()}
    else:
        enabled = {s.strip() for s in configured if s.strip()}

    collected: list[str] = []

    if "hyde" in enabled:
        hyde_docs = hyde(query)
        if hyde_docs:
            collected.extend(hyde_docs)
        collected.append(query)

    if "sub_queries" in enabled:
        for sq in sub_queries(query):
            collected.append(sq)

    if "stepback" in enabled:
        sb = stepback(query)
        if sb:
            collected.append(sb)
        collected.append(query)

    if not collected:
        return [query]

    seen: set[str] = set()
    result: list[str] = []
    for q in collected:
        if q not in seen:
            seen.add(q)
            result.append(q)
    return result
