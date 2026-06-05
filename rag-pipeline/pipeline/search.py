"""Search orchestrator for the RAG pipeline.

Full pipeline:
  query enhancement → embed query → hybrid/vector/BM25 retrieval
  → RRF fusion → deduplication → optional reranking → parent context fetch
  → return top results.

The :func:`search` function is the main entry point.
:func:`format_results` formats results for CLI display.
"""

import uuid

import openai

from .config import cfg
from .models import Chunk, ChunkType, SearchResult
from .embed import embed_text
from .retrieve import hybrid_search, rerank, vector_search, bm25_search, _rrf_fusion
from .query import enhance_query
from .index import connect_milvus, _ensure_collection, get_client


def search(
    query: str,
    top_k: int | None = None,
    use_reranker: bool = True,
    retrieval_mode: str = "hybrid",
) -> list[SearchResult]:
    """Full search pipeline.

    Steps
    -----
    1. Connect to Milvus.
    2. Apply query enhancements: ``enhanced_queries = enhance_query(query)``.
    3. For each enhanced query string:
       a. Build a temporary :class:`~pipeline.models.Chunk` from the query text
          and call :func:`~pipeline.embed.embed_text` to obtain its embedding.
       b. Run retrieval according to *retrieval_mode*:
          - ``"vector"``  – :func:`~pipeline.retrieve.vector_search`
          - ``"bm25"``    – :func:`~pipeline.retrieve.bm25_search`
          - ``"hybrid"``  – :func:`~pipeline.retrieve.hybrid_search`
    4. Merge all per-query result lists with Reciprocal Rank Fusion via
       :func:`~pipeline.retrieve._rrf_fusion`.
    5. Deduplicate by ``chunk.id`` (keep highest score).
    6. Optionally rerank with :func:`~pipeline.retrieve.rerank` using the
       original *query* (not the enhanced variants).
    7. For ``ChunkType.CHILD`` results whose ``parent_id`` is set, attempt to
       fetch the parent chunk from Milvus and store its text in
       ``chunk.window_text`` (if not already populated).  Errors during
       parent fetch are silently ignored.
    8. Return the top ``cfg.rerank_top_k`` results (or *top_k* if supplied).

    Args:
        query:          The user's search query.
        top_k:          Maximum number of results to return.  Overrides
                        ``cfg.rerank_top_k`` when given.
        use_reranker:   Whether to run the reranker after fusion.
        retrieval_mode: One of ``"vector"``, ``"bm25"``, or ``"hybrid"``.

    Returns:
        Ordered list of :class:`~pipeline.models.SearchResult` objects.
    """
    connect_milvus()

    effective_top_k = top_k if top_k is not None else cfg.rerank_top_k
    retrieval_k = cfg.retrieval_top_k

    enhanced_queries = enhance_query(query)

    # ------------------------------------------------------------------
    # Per-query retrieval
    # ------------------------------------------------------------------
    all_result_lists: list[list[SearchResult]] = []

    for q_text in enhanced_queries:
        # Build a temporary chunk so we can reuse embed_text's batch API.
        tmp_chunk = Chunk(
            id=uuid.uuid4().hex,
            source_path="__query__",
            text=q_text,
            chunk_type=ChunkType.TEXT,
        )
        embedded = embed_text([tmp_chunk])
        if not embedded:
            continue
        embedding = embedded[0].embedding

        mode = retrieval_mode.lower()
        if mode == "vector":
            results = vector_search(embedding, retrieval_k)
        elif mode == "bm25":
            results = bm25_search(q_text, retrieval_k)
        else:  # default: hybrid
            results = hybrid_search(q_text, embedding, retrieval_k)

        if results:
            all_result_lists.append(results)

    if not all_result_lists:
        return []

    # ------------------------------------------------------------------
    # Merge with RRF fusion
    # ------------------------------------------------------------------
    merged = _rrf_fusion(all_result_lists)

    # ------------------------------------------------------------------
    # Deduplicate by chunk.id (keep entry with highest score)
    # ------------------------------------------------------------------
    seen: dict[str, SearchResult] = {}
    for sr in merged:
        cid = sr.chunk.id
        if cid not in seen or sr.score > seen[cid].score:
            seen[cid] = sr
    deduped = sorted(seen.values(), key=lambda r: r.score, reverse=True)

    # Re-assign ranks after deduplication.
    for i, sr in enumerate(deduped):
        sr.rank = i + 1

    # ------------------------------------------------------------------
    # Optional reranking (uses original query, not enhanced variants)
    # ------------------------------------------------------------------
    if use_reranker and deduped:
        deduped = rerank(query, deduped, top_k=effective_top_k)

    # ------------------------------------------------------------------
    # Parent context fetch for CHILD chunks
    # ------------------------------------------------------------------
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
                pass  # Parent fetch is best-effort; never fail the search.

    return deduped[:effective_top_k]


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
            f"[{sr.rank}] score={sr.score:.4f}  method={sr.retrieval_method}"
        )
        lines.append(f"    source: {chunk.source_path}")
        lines.append(f"    type:   {chunk.chunk_type.value}")
        if chunk.parent_id:
            lines.append(f"    parent: {chunk.parent_id}")
        lines.append("")
        lines.append(display_text)

    lines.append(separator)
    return "\n".join(lines)
