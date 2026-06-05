"""Query enhancement strategies for the RAG pipeline.

Provides four enhancement functions (HyDE, sub-queries, stepback,
hypothetical questions) and a single public ``enhance_query`` entry point
that applies whichever strategies are enabled in ``cfg.query_enhancements``.
"""

import litellm

from .config import cfg


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _chat(system: str, user: str) -> str:
    """Call the chat model and return the response text.

    Non-streaming.  Uses ``cfg.chat_model`` as the model name.
    """
    response = litellm.completion(
        model=cfg.chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        api_base=cfg.chat_base_url,
        api_key=cfg.chat_api_key,
        stream=False,
    )
    return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Enhancement functions
# ---------------------------------------------------------------------------

def hyde(query: str) -> str:
    """Hypothetical Document Embeddings (HyDE).

    Asks the LLM to write a short hypothetical document or passage that
    would directly answer the query.  The returned text is intended to be
    embedded and used for retrieval *instead of* the raw query, because a
    hypothetical answer lives in the same embedding space as real documents.

    Args:
        query: The user's original search query.

    Returns:
        A short hypothetical document / passage as a plain string.
    """
    system = (
        "You are a helpful assistant that generates hypothetical documents. "
        "When given a query, write a short, realistic passage (2-4 sentences) "
        "that would directly answer or address the query. "
        "Write only the passage itself — no preamble, no explanation."
    )
    user = f"Query: {query}"
    return _chat(system, user).strip()


def sub_queries(query: str) -> list[str]:
    """Decompose a complex query into 2-4 simpler sub-queries.

    The LLM is prompted to break the query down into independent sub-questions,
    each on its own line starting with a '-' or a number.  Lines that do not
    start with '-' or a digit are ignored.  The original query is always
    appended as the final element.

    Args:
        query: The user's original search query.

    Returns:
        A list of sub-query strings (may overlap), with the original query
        appended last.
    """
    system = (
        "You are an expert at breaking down complex questions. "
        "Given a query, decompose it into 2-4 simpler, focused sub-questions "
        "that together cover all aspects of the original question. "
        "Return each sub-question on its own line, prefixed with a '-'. "
        "Output only the sub-questions, nothing else."
    )
    user = f"Query: {query}"
    raw = _chat(system, user)

    results: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Accept lines starting with '-' or a digit (e.g. "1.", "2)")
        if stripped.startswith("-"):
            q = stripped.lstrip("-").strip()
            if q:
                results.append(q)
        elif stripped and stripped[0].isdigit():
            # Strip leading "1." / "1)" / "1:" etc.
            q = stripped.lstrip("0123456789").lstrip(".):- ").strip()
            if q:
                results.append(q)

    # Always include the original query last
    results.append(query)
    return results


def stepback(query: str) -> str:
    """Generate a broader 'stepback' question.

    Reformulates the query as a more general question that provides the
    high-level background knowledge needed to answer the original query.
    Retrieving documents for this broader question can surface context that
    would otherwise be missed.

    Args:
        query: The user's original search query.

    Returns:
        A single, more general reformulation of the query.
    """
    system = (
        "You are an expert at reformulating questions. "
        "Given a specific query, produce a broader, more general question "
        "that captures the underlying concept or domain. "
        "The broader question should help surface background knowledge "
        "useful for answering the original query. "
        "Return only the broader question, nothing else."
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

def enhance_query(query: str) -> list[str]:
    """Apply all enabled query enhancements from ``cfg.query_enhancements``.

    ``cfg.query_enhancements`` is a comma-separated string of strategy names.
    Recognised names: ``"hyde"``, ``"sub_queries"``, ``"stepback"``,
    ``"hypothetical_questions"``.

    Strategy behaviour:
    - ``hyde``:                  Replace the query with the HyDE document.
    - ``sub_queries``:           Add the decomposed sub-queries.
    - ``stepback``:              Add the stepback question *and* the original.
    - ``hypothetical_questions``: Ignored at query time (index-time only).

    If no strategies are enabled (or the config value is empty / unknown),
    returns ``[query]``.

    Duplicate strings are removed while preserving order.

    Args:
        query: The user's original search query.

    Returns:
        A deduplicated list of query strings to use for retrieval.
    """
    enabled = {s.strip() for s in cfg.query_enhancements.split(",") if s.strip()}

    collected: list[str] = []

    if "hyde" in enabled:
        hyde_doc = hyde(query)
        if hyde_doc:
            collected.append(hyde_doc)

    if "sub_queries" in enabled:
        for sq in sub_queries(query):
            collected.append(sq)

    if "stepback" in enabled:
        sb = stepback(query)
        if sb:
            collected.append(sb)
        collected.append(query)

    # hypothetical_questions is index-time only; skip at query time.

    # If nothing was added by any strategy, fall back to the original query.
    if not collected:
        return [query]

    # Deduplicate while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for q in collected:
        if q not in seen:
            seen.add(q)
            result.append(q)
    return result
