import json
import re
from collections import defaultdict

import httpx

from .config import cfg
from .index import _chunk_from_hit, _ensure_collection, get_client, load_bm25_index
from .models import Chunk, ChunkType, EmbeddedChunk, SearchResult


_LEXICAL_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "explain", "give", "in", "is", "it", "me", "mentioned", "of", "on",
    "please", "summarize", "tell", "that", "the", "this", "to", "was",
    "what", "with",
}


def scoped_lexical_search(
    query: str,
    source_paths: list[str] | set[str],
    top_k: int = 8,
) -> list[SearchResult]:
    """Find rare literal facts and structural references inside selected files.

    The persisted BM25 index can lag newly ingested documents. For an explicit
    document scope, scan that document's indexed chunk text and rank chunks by
    query-term coverage. This catches needle facts and references such as
    "chapter 3" without widening retrieval to other documents.
    """
    paths = list(dict.fromkeys(source_paths))
    if not paths:
        return []

    terms = list(dict.fromkeys(
        token
        for token in re.findall(r"[a-z0-9]+", query.casefold())
        if token not in _LEXICAL_STOPWORDS and (len(token) > 1 or token.isdigit())
    ))
    if not terms:
        return []

    escaped = [path.replace("\\", "\\\\").replace('"', '\\"') for path in paths]
    path_list = "[" + ", ".join(f'"{path}"' for path in escaped) + "]"
    hits = get_client().query(
        collection_name=cfg.text_collection,
        filter=f"source_path in {path_list}",
        output_fields=[
            "id", "source_path", "text", "chunk_type", "parent_id",
            "window_text", "metadata_json",
        ],
        limit=10000,
    )

    candidates: list[tuple[float, int, int, Chunk]] = []
    minimum_matches = 1 if len(terms) == 1 else 2
    phrase = " ".join(terms)
    for hit in hits:
        chunk = _chunk_from_hit(hit)
        if chunk.chunk_type in {ChunkType.SUMMARY, ChunkType.HYPOTHETICAL_QUESTION}:
            continue
        text = chunk.text.casefold()
        text_tokens = set(re.findall(r"[a-z0-9]+", text))
        matched = sum(term in text_tokens for term in terms)
        if matched < minimum_matches:
            continue
        phrase_bonus = 2 if phrase and phrase in text else 0
        non_parent_bonus = 1 if chunk.chunk_type != ChunkType.PARENT else 0
        first_match = min((text.find(term) for term in terms if term in text), default=len(text))
        score = float(matched * 10 + phrase_bonus + non_parent_bonus)
        candidates.append((score, non_parent_bonus, -first_match, chunk))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [
        SearchResult(
            chunk=chunk,
            score=score,
            rank=rank,
            retrieval_method="scoped_lexical",
        )
        for rank, (score, _, _, chunk) in enumerate(candidates[:top_k])
    ]


def vector_search(
    query_embedding: list[float],
    top_k: int | None = None,
    collection_name: str | None = None,
    extra_filter: str | None = None,
) -> list[SearchResult]:
    if top_k is None:
        top_k = cfg.retrieval_top_k
    if collection_name is None:
        collection_name = cfg.text_collection

    dim = (
        cfg.text_embedding_dim
        if collection_name == cfg.text_collection
        else cfg.multimodal_embedding_dim
    )
    _ensure_collection(collection_name, dim)

    output_fields = [
        "id", "source_path", "text", "chunk_type",
        "parent_id", "window_text", "metadata_json",
    ]
    search_kwargs = dict(
        collection_name=collection_name,
        data=[query_embedding],
        anns_field="embedding",
        search_params={"metric_type": "IP", "params": {"nprobe": 10}},
        limit=top_k,
        output_fields=output_fields,
    )
    if extra_filter:
        search_kwargs["filter"] = extra_filter
    results = get_client().search(**search_kwargs)

    search_results: list[SearchResult] = []
    if results:
        for rank, hit in enumerate(results[0]):
            chunk = _chunk_from_hit(hit)
            search_results.append(
                SearchResult(
                    chunk=chunk,
                    score=hit["distance"],
                    rank=rank,
                    retrieval_method="vector",
                )
            )

    return search_results


def bm25_search(
    query: str,
    top_k: int | None = None,
    source_paths: list[str] | set[str] | None = None,
) -> list[SearchResult]:
    """BM25 sparse search using the persisted index.

    Returns an empty list (instead of raising) when no BM25 index has been
    built yet — this happens on a fresh deployment before any documents are
    ingested.

    Tokenizes query the same way as indexing: query.lower().split().
    Returns SearchResult list with retrieval_method="bm25".
    """
    if top_k is None:
        top_k = cfg.retrieval_top_k

    try:
        bm25, chunks = load_bm25_index()
    except FileNotFoundError:
        return []
    tokenized_query = query.lower().split()
    scores = bm25.get_scores(tokenized_query)

    # Pair each allowed chunk with its score and sort descending. Filename
    # scoping must apply to both halves of hybrid retrieval, not only Milvus.
    allowed_paths = set(source_paths) if source_paths is not None else None
    scored = sorted(
        (
            (index, score)
            for index, score in enumerate(scores)
            if allowed_paths is None or chunks[index].source_path in allowed_paths
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    top_scored = scored[:top_k]

    search_results: list[SearchResult] = []
    for rank, (idx, score) in enumerate(top_scored):
        search_results.append(
            SearchResult(
                chunk=chunks[idx],
                score=float(score),
                rank=rank,
                retrieval_method="bm25",
            )
        )

    return search_results


def _rrf_fusion(
    results_lists: list[list[SearchResult]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    score(d) = sum(weight_i / (k + rank_i(d))) for each list that contains d.

    weights must have the same length as results_lists.  Defaults to uniform
    weights (1.0 per list).  Pass [cfg.hybrid_alpha, 1-cfg.hybrid_alpha] for
    vector/BM25 fusion with alpha control.
    """
    if weights is None:
        weights = [1.0] * len(results_lists)

    rrf_scores: dict[str, float] = defaultdict(float)
    chunks_by_id: dict[str, Chunk] = {}

    if len(weights) != len(results_lists):
        raise ValueError("weights must have the same length as results_lists")

    for result_list, weight in zip(results_lists, weights):
        for result in result_list:
            chunk_id = result.chunk.id
            # rank is 0-based; RRF formula uses 1-based rank.
            rrf_scores[chunk_id] += weight / (k + result.rank + 1)
            if chunk_id not in chunks_by_id:
                chunks_by_id[chunk_id] = result.chunk

    sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)

    fused: list[SearchResult] = []
    for rank, chunk_id in enumerate(sorted_ids):
        fused.append(
            SearchResult(
                chunk=chunks_by_id[chunk_id],
                score=rrf_scores[chunk_id],
                rank=rank,
                retrieval_method="hybrid",
            )
        )

    return fused


def hybrid_search(
    query: str,
    query_embedding: list[float],
    top_k: int | None = None,
    extra_filter: str | None = None,
    source_paths: list[str] | set[str] | None = None,
) -> list[SearchResult]:
    """Hybrid (vector + BM25) parallel retrieval.

    Runs dense semantic search and sparse keyword search simultaneously,
    then fuses results via weighted Reciprocal Rank Fusion (RRF).

    The fused list includes **all** candidates from both channels (up to
    ``2 × top_k`` items).  No early trimming — the reranker (if enabled)
    acts as the central fusion node, scoring every candidate before the
    final top-K selection.  See ``docs/architecture.md`` → *Hybrid
    Retrieve & Reranking Workflow*.

    Args:
        query: Raw query text (for BM25).
        query_embedding: Dense embedding (for vector search).
        top_k: How many candidates each sub-channel retrieves.  Defaults
               to ``cfg.retrieval_top_k``.
        extra_filter: Optional Milvus scalar filter expression forwarded
                      to :func:`vector_search`.
        source_paths: Optional exact source-path scope applied to BM25.

    Returns:
        RRF-fused list of :class:`SearchResult` objects from both
        channels.  Length can be up to ``2 × top_k`` (minus any chunks
        returned by both channels, which are deduplicated by RRF).
    """
    if top_k is None:
        top_k = cfg.retrieval_top_k

    vector_results = vector_search(query_embedding, top_k=top_k, extra_filter=extra_filter)
    bm25_results = bm25_search(query, top_k=top_k, source_paths=source_paths)

    # cfg.hybrid_alpha: 1.0 = pure vector, 0.0 = pure BM25.
    fused = _rrf_fusion(
        [vector_results, bm25_results],
        weights=[cfg.hybrid_alpha, 1.0 - cfg.hybrid_alpha],
    )

    # No trim — every candidate from both channels goes to the caller so
    # that the reranker (if enabled downstream) can score them all before
    # the final top-K selection.
    return fused


def rerank(
    query: str,
    results: list[SearchResult],
    top_k: int | None = None,
) -> list[SearchResult]:
    """Rerank results using the vLLM score API endpoint.

    Primary: POST {cfg.reranker_base_url}/score
    Body: {
        "model": cfg.reranker_model,
        "text_1": [query, query, ...],   # repeated for each document
        "text_2": ["doc1", "doc2", ...],
    }
    Response: {"data": [{"index": int, "score": float, ...}, ...]}

    Falls back to POST {cfg.reranker_base_url}/v1/rerank (original format) if
    /score returns 404 (e.g. mock server that does not implement /score).

    Maps response items back to SearchResult using the index into the original
    results list.  Sets retrieval_method="reranked".
    """
    if top_k is None:
        top_k = cfg.rerank_top_k

    if not results:
        return []

    headers = {
        "Authorization": f"Bearer {cfg.reranker_api_key}",
        "Content-Type": "application/json",
    }
    documents = [r.chunk.text for r in results]

    # ------------------------------------------------------------------
    # POST {reranker_base_url}/score  (vLLM batch scoring format)
    # queries[i] scores against documents[i] (1-to-1 mapping)
    # ------------------------------------------------------------------
    score_url = f"{cfg.reranker_base_url.rstrip('/')}/score"
    score_payload = {
        "model": cfg.reranker_model,
        "queries": [query] * len(results),
        "documents": documents,
    }

    response = httpx.post(score_url, json=score_payload, headers=headers, timeout=30)
    response.raise_for_status()
    response_data = response.json()

    scored_items = [
        (item["index"], item.get("score", 0.0))
        for item in response_data.get("data", [])
    ]

    # Sort by score descending (response order is not guaranteed)
    scored_items.sort(key=lambda x: x[1], reverse=True)

    reranked: list[SearchResult] = []
    for rank, (original_index, score) in enumerate(scored_items[:top_k]):
        original_result = results[original_index]
        reranked.append(
            SearchResult(
                chunk=original_result.chunk,
                score=score,
                rank=rank,
                retrieval_method="reranked",
            )
        )

    return reranked
