import json
from collections import defaultdict

import httpx

from .config import cfg
from .index import _chunk_from_hit, _ensure_collection, get_client, load_bm25_index
from .models import Chunk, EmbeddedChunk, SearchResult


def vector_search(
    query_embedding: list[float],
    top_k: int | None = None,
    collection_name: str | None = None,
) -> list[SearchResult]:
    """Search Milvus for nearest neighbors using inner product (cosine on normalized vectors).

    collection_name defaults to cfg.text_collection.
    Returns SearchResult list with retrieval_method="vector".
    """
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
    results = get_client().search(
        collection_name=collection_name,
        data=[query_embedding],
        anns_field="embedding",
        search_params={"metric_type": "IP", "params": {"nprobe": 10}},
        limit=top_k,
        output_fields=output_fields,
    )

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


def bm25_search(query: str, top_k: int | None = None) -> list[SearchResult]:
    """BM25 sparse search using the persisted index.

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

    # Pair each chunk with its score and sort descending.
    scored = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
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

    score(d) = sum(1 / (k + rank_i(d))) for each list that contains d.
    Returns merged list sorted by fused score descending, with retrieval_method="hybrid".
    """
    # Map chunk id → cumulative RRF score and a representative chunk.
    rrf_scores: dict[str, float] = defaultdict(float)
    chunks_by_id: dict[str, Chunk] = {}

    if weights is None:
        weights = [1.0] * len(results_lists)
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
) -> list[SearchResult]:
    """Combine vector_search and bm25_search via RRF fusion.

    cfg.hybrid_alpha weights the two lists (alpha=1 → pure vector, alpha=0 → pure BM25),
    but both lists are always passed to _rrf_fusion; alpha is reflected by controlling
    how many results each source contributes.
    """
    if top_k is None:
        top_k = cfg.retrieval_top_k

    vector_results = vector_search(query_embedding, top_k=top_k)
    bm25_results = bm25_search(query, top_k=top_k)

    fused = _rrf_fusion(
        [vector_results, bm25_results],
        weights=[cfg.hybrid_alpha, 1.0 - cfg.hybrid_alpha],
    )

    # Trim to requested top_k.
    return fused[:top_k]


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
    # Try the vLLM /score endpoint first
    # ------------------------------------------------------------------
    score_url = f"{cfg.reranker_base_url.rstrip('/')}/score"
    score_payload = {
        "model": cfg.reranker_model,
        "text_1": [query] * len(results),
        "text_2": documents,
    }

    response = httpx.post(score_url, json=score_payload, headers=headers, timeout=30)

    if response.status_code == 404:
        # Fall back to the legacy /v1/rerank format (e.g. mock server)
        rerank_url = f"{cfg.reranker_base_url.rstrip('/')}/v1/rerank"
        rerank_payload = {
            "model": cfg.reranker_model,
            "query": query,
            "documents": documents,
            "top_n": top_k,
        }
        response = httpx.post(rerank_url, json=rerank_payload, headers=headers, timeout=30)
        response.raise_for_status()
        response_data = response.json()

        scored_items = [
            (item["index"], item.get("relevance_score", 0.0))
            for item in response_data.get("results", [])
        ]
    else:
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
