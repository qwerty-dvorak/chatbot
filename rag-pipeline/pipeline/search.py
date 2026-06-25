"""Search orchestrator for the RAG pipeline.

Full pipeline:
  query enhancement → embed query → hybrid/vector/BM25 retrieval
  → RRF fusion → deduplication → optional reranking → parent context fetch
  → return top results.

The :func:`search` function is the main entry point.
:func:`format_results` formats results for CLI display.
"""

import re
import time
import uuid

from .config import cfg
from .models import Chunk, ChunkType, SearchResult
from .embed import embed_text
from .retrieve import (
    _rrf_fusion,
    bm25_search,
    hybrid_search,
    rerank,
    scoped_lexical_search,
    vector_search,
)
from .query import enhance_query
from .index import connect_milvus, _ensure_collection, _chunk_from_hit, get_client


def search(
    query: str,
    top_k: int | None = None,
    use_reranker: bool = True,
    retrieval_mode: str = "hybrid",
<<<<<<< Updated upstream
    enhancements: str | list[str] | None = None,
    hierarchical: bool | None = None,
    artifact_sources: list[str] | None = None,
) -> tuple[list[SearchResult], dict[str, float]]:
=======
    enhancements: list[str] | None = None,
    use_chatbot_llm: bool = False,
) -> list[SearchResult]:
>>>>>>> Stashed changes
    """Full search pipeline.

    Steps
    -----
    1. Connect to Milvus.
    2. Apply query enhancements: ``enhanced_queries = enhance_query(query, ...)``.
    3. For each enhanced query string:
       a. Build a temporary :class:`~pipeline.models.Chunk` from the query text
          and call :func:`~pipeline.embed.embed_text` to obtain its embedding.
       b. If *hierarchical* is ``True`` (default from config):
          i.   **Stage 1** — Search against ``chunk_type == "summary"`` vectors
               to identify top-N relevant documents (N = ``summary_top_k``).
          ii.  Extract the ``source_path`` values from matched summaries.
          iii. **Stage 2** — Search only within those documents' chunks
               (filter: ``source_path in [...] and chunk_type != "summary"``).
       c. Otherwise (standard mode), run retrieval directly according to
          *retrieval_mode*:
          - ``"vector"``  – :func:`~pipeline.retrieve.vector_search`
          - ``"bm25"``    – :func:`~pipeline.retrieve.bm25_search`
          - ``"hybrid"``  – :func:`~pipeline.retrieve.hybrid_search`
    4. Merge all per-query result lists with Reciprocal Rank Fusion.
    5. Deduplicate by ``chunk.id`` (keep highest score).
    6. Optionally rerank with :func:`~pipeline.retrieve.rerank` using the
       original *query* (not the enhanced variants).
    7. For ``ChunkType.CHILD`` results whose ``parent_id`` is set, attempt to
       fetch the parent chunk from Milvus and store its text in
       ``chunk.window_text`` (if not already populated).
    8. Return the top ``cfg.rerank_top_k`` results (or *top_k* if supplied).

    Args:
<<<<<<< Updated upstream
        query:          The user's search query.
        top_k:          Maximum number of results to return.  Overrides
                        ``cfg.rerank_top_k`` when given.
        use_reranker:   Whether to run the reranker after fusion.
        retrieval_mode: One of ``"vector"``, ``"bm25"``, or ``"hybrid"``.
        hierarchical:   Enable two-stage coarse-to-fine search (stage 1 =
                        summary vectors; stage 2 = document chunks within
                        matched documents).  ``None`` falls back to
                        ``cfg.hierarchical_mode``.
=======
        query:            The user's search query.
        top_k:            Maximum results.  Overrides ``cfg.rerank_top_k``.
        use_reranker:     Whether to run the reranker after fusion.
        retrieval_mode:   ``"vector"``, ``"bm25"``, or ``"hybrid"``.
        enhancements:     Explicit list of enhancement names to apply (e.g.
                          ``["hyde", "sub_queries"]``).  ``None`` reads from
                          ``cfg.query_enhancements``.  Pass ``[]`` to disable
                          all enhancements for instant-tier speed.
        use_chatbot_llm:  When True, enhancement LLM calls use the chatbot-
                          service endpoint (``cfg.chatbot_llm_*``).  For
                          global-tier searches.
>>>>>>> Stashed changes

    Returns:
        Tuple of ``(list[SearchResult], timing_dict)`` where *timing_dict*
        maps step names to seconds.
    """
    timing: dict[str, float] = {}
    _t = time.time

    connect_milvus()

    # Fast return if vector store is empty
    t0 = _t()
    try:
        _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
        total_vectors = len(get_client().query(
            collection_name=cfg.text_collection,
            output_fields=["id"],
            limit=10000,
        ))
    except Exception:
        total_vectors = 0
    if total_vectors == 0:
        return [], {"empty_store": 0.001, "total": 0.001, "message": "Vector store is empty — ingest documents first"}

    effective_top_k = top_k if top_k is not None else cfg.rerank_top_k
    retrieval_k = cfg.retrieval_top_k

<<<<<<< Updated upstream
    if hierarchical is None:
        hierarchical = cfg.hierarchical_mode

    mode = retrieval_mode.lower()
    if mode not in {"vector", "bm25", "hybrid"}:
        raise ValueError("retrieval_mode must be one of: vector, bm25, hybrid")

    t0 = _t()
    enhanced_queries = enhance_query(query, enhancements=enhancements)
    timing["enhance_query"] = round(_t() - t0, 4)
=======
    enhanced_queries = enhance_query(
        query,
        enhancements=enhancements,
        use_chatbot_llm=use_chatbot_llm,
    )
>>>>>>> Stashed changes

    # ------------------------------------------------------------------
    # Per-query retrieval
    # ------------------------------------------------------------------
    t_embed_total = 0.0
    t_search_total = 0.0
    all_result_lists: list[list[SearchResult]] = []
    n_queries = len(enhanced_queries)

    # Resolve requested filenames once for every enhanced query. This scope is
    # mandatory: no matching vectors means no results, never a global fallback.
    artifact_paths: list[str] | None = None
    if artifact_sources:
        t0 = _t()
        _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
        hits = get_client().query(
            collection_name=cfg.text_collection,
            output_fields=["source_path"],
            limit=10000,
        )
        requested_names = {source.casefold() for source in artifact_sources}
        artifact_paths = list({
            hit["source_path"]
            for hit in hits
            if hit.get("source_path", "").replace("\\", "/").rsplit("/", 1)[-1].casefold()
            in requested_names
        })
        timing["artifact_filter"] = round(_t() - t0, 4)
        if not artifact_paths:
            timing["total"] = round(sum(timing.values()), 4)
            return [], timing

    lexical_results: list[SearchResult] = []
    if artifact_paths:
        t0 = _t()
        lexical_query = query
        for source in artifact_sources or []:
            stem = source.rsplit(".", 1)[0]
            lexical_query = re.sub(re.escape(source), " ", lexical_query, flags=re.IGNORECASE)
            lexical_query = re.sub(re.escape(stem), " ", lexical_query, flags=re.IGNORECASE)
        lexical_results = scoped_lexical_search(
            " ".join(lexical_query.split()),
            artifact_paths,
            top_k=max(effective_top_k, 8),
        )
        timing["scoped_lexical"] = round(_t() - t0, 4)

    for q_text in enhanced_queries:
        tmp_chunk = Chunk(
            id=uuid.uuid4().hex,
            source_path="__query__",
            text=q_text,
            chunk_type=ChunkType.TEXT,
        )
        t0 = _t()
        embedded = embed_text([tmp_chunk])
        t_embed_total += _t() - t0
        if not embedded:
            continue
        embedding = embedded[0].embedding

<<<<<<< Updated upstream
        artifact_filter = ""
        if artifact_paths:
            escaped = [p.replace("\\", "\\\\").replace('"', '\\"') for p in artifact_paths]
            path_list = "[" + ", ".join(f'"{e}"' for e in escaped) + "]"
            artifact_filter = f"source_path in {path_list}"

        # ── Two-stage hierarchical search ──────────────────────────
        t0 = _t()
        if hierarchical and not artifact_filter:
            summary_hits = vector_search(
                embedding, top_k=cfg.summary_top_k,
                extra_filter='chunk_type == "summary"',
            )
            matched_paths = list({h.chunk.source_path for h in summary_hits})
            if matched_paths:
                escaped = [
                    p.replace("\\", "\\\\").replace('"', '\\"')
                    for p in matched_paths
                ]
                path_list = "[" + ", ".join(f'"{e}"' for e in escaped) + "]"
                extra = f"source_path in {path_list} and chunk_type != \"summary\""
                if mode == "vector":
                    results = vector_search(embedding, retrieval_k, extra_filter=extra)
                elif mode == "bm25":
                    results = bm25_search(q_text, retrieval_k, source_paths=matched_paths)
                else:
                    results = hybrid_search(
                        q_text,
                        embedding,
                        retrieval_k,
                        extra_filter=extra,
                        source_paths=matched_paths,
                    )
            else:
                results = []
        else:
            extra = artifact_filter if artifact_filter else None
            if mode == "vector":
                results = vector_search(embedding, retrieval_k, extra_filter=extra)
            elif mode == "bm25":
                results = bm25_search(q_text, retrieval_k, source_paths=artifact_paths)
            else:
                results = hybrid_search(
                    q_text,
                    embedding,
                    retrieval_k,
                    extra_filter=extra,
                    source_paths=artifact_paths,
                )
        t_search_total += _t() - t0
=======
        mode = retrieval_mode.lower()
        if mode == "vector":
            results = vector_search(embedding, retrieval_k)
        elif mode == "bm25":
            results = bm25_search(q_text, retrieval_k)
        else:
            results = hybrid_search(q_text, embedding, retrieval_k)
>>>>>>> Stashed changes

        if results:
            all_result_lists.append(results)

    timing["embed"] = round(t_embed_total, 4)
    timing["search"] = round(t_search_total, 4)

    if not all_result_lists:
        timing["total"] = round(sum(timing.values()), 4)
        return lexical_results[:effective_top_k], timing

    # ------------------------------------------------------------------
    # Merge with RRF fusion
    # ------------------------------------------------------------------
    t0 = _t()
    merged = _rrf_fusion(all_result_lists)
    timing["fusion"] = round(_t() - t0, 4)

    # ------------------------------------------------------------------
    # Resolve hypothetical-question hits to parent chunks, then
    # deduplicate by chunk.id (keep entry with highest score).
    # ------------------------------------------------------------------
    t0 = _t()
    seen: dict[str, SearchResult] = {}
    for sr in merged:
        chunk = sr.chunk
        if chunk.chunk_type == ChunkType.HYPOTHETICAL_QUESTION and chunk.parent_id:
            try:
                _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
                hits = list(get_client().query(
                    collection_name=cfg.text_collection,
                    filter=f'id == "{chunk.parent_id}"',
                    output_fields=["id", "text", "source_path", "chunk_type",
                                   "parent_id", "window_text", "metadata_json"],
                    limit=1,
                ))
                if hits:
                    parent = _chunk_from_hit(hits[0])
                    sr.chunk = parent
                    sr.retrieval_method = "query_to_query"
                    chunk = parent
            except Exception:
                pass
        cid = chunk.id
        if cid not in seen or sr.score > seen[cid].score:
            seen[cid] = sr
    deduped = sorted(seen.values(), key=lambda r: r.score, reverse=True)
<<<<<<< Updated upstream
=======

>>>>>>> Stashed changes
    for i, sr in enumerate(deduped):
        sr.rank = i
    timing["dedup"] = round(_t() - t0, 4)

    # ------------------------------------------------------------------
    # Optional reranking (uses original query, not enhanced variants)
    # ------------------------------------------------------------------
    if use_reranker and deduped:
        t0 = _t()
        deduped = rerank(query, deduped, top_k=effective_top_k)
        timing["rerank"] = round(_t() - t0, 4)

    # ------------------------------------------------------------------
    # Parent context fetch for CHILD chunks
    # ------------------------------------------------------------------
    t0 = _t()
    for sr in deduped:
        chunk = sr.chunk
        if (
            chunk.chunk_type == ChunkType.CHILD
            and chunk.parent_id
            and not chunk.window_text
        ):
            try:
                _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
                hits = list(get_client().query(
                    collection_name=cfg.text_collection,
                    filter=f'id == "{chunk.parent_id}"',
                    output_fields=["id", "text", "source_path", "chunk_type",
                                   "parent_id", "window_text", "metadata_json"],
                    limit=1,
                ))
                if hits:
                    parent_text = hits[0].get("text", "")
                    if parent_text:
                        chunk.window_text = parent_text
            except Exception:  # noqa: BLE001
                pass
<<<<<<< Updated upstream
    timing["parent_fetch"] = round(_t() - t0, 4)
=======
>>>>>>> Stashed changes

    # Exact scoped matches are deterministic evidence and must survive semantic
    # reranking. Place them first, then append non-duplicate semantic results.
    if lexical_results:
        lexical_ids = {result.chunk.id for result in lexical_results}
        deduped = lexical_results + [
            result for result in deduped if result.chunk.id not in lexical_ids
        ]
        for rank, result in enumerate(deduped):
            result.rank = rank

    timing["total"] = round(sum(timing.values()), 4)
    return deduped[:effective_top_k], timing


def format_results(results: list[SearchResult]) -> str:
    """Format a list of :class:`~pipeline.models.SearchResult` objects for
    CLI display.

    Each result is rendered as a numbered block containing the rank, score,
    retrieval method, source path, and the chunk text (truncated to 500
    characters).  If ``window_text`` is set it is shown instead of the raw
    chunk text.

    Args:
        results: Ordered list of search results (e.g. from :func:`search`).

    Returns:
        A single string ready to be printed.
    """
    if not results:
        return "No results found."

    lines: list[str] = []
    separator = "-" * 60

    for sr in results:
        chunk = sr.chunk
        display_text = (chunk.window_text or chunk.text).strip()
        if len(display_text) > 500:
            display_text = display_text[:500] + "..."

        lines.append(separator)
        lines.append(
            f"[{sr.rank + 1}] score={sr.score:.4f}  method={sr.retrieval_method}"
        )
        lines.append(f"    source: {chunk.source_path}")
        lines.append(f"    type:   {chunk.chunk_type.value}")
        if chunk.parent_id:
            lines.append(f"    parent: {chunk.parent_id}")
        lines.append("")
        lines.append(display_text)

    lines.append(separator)
    return "\n".join(lines)
