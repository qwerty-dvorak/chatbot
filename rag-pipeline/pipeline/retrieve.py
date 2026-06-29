"""Retrieval strategies: vector, BM25, hybrid, and reranking."""

from collections import defaultdict

import httpx

from .bm25 import load_index as load_bm25_index
from .config import cfg
from .milvus import chunk_from_hit as _chunk_from_hit
from .milvus import ensure_collection as _ensure_collection
from .milvus import get_client
from .models import Chunk, SearchResult

_LEXICAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "explain",
    "give",
    "in",
    "is",
    "it",
    "me",
    "mentioned",
    "of",
    "on",
    "please",
    "summarize",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "was",
    "what",
    "with",
}


def vector_search(
    query_embedding: list[float],
    top_k: int | None = None,
    collection_name: str | None = None,
    extra_filter: str | None = None,
) -> list[SearchResult]:
    """Search Milvus using vector similarity."""
    if top_k is None:
        top_k = cfg.retrieval_top_k
    if collection_name is None:
        collection_name = cfg.text_collection
    dim = cfg.text_embedding_dim if collection_name == cfg.text_collection else cfg.multimodal_embedding_dim
    _ensure_collection(collection_name, dim)
    output_fields = ["id", "source_path", "text", "chunk_type", "parent_id", "window_text", "metadata_json"]
    search_kwargs = {
        "collection_name": collection_name,
        "data": [query_embedding],
        "anns_field": "embedding",
        "search_params": {"metric_type": "IP", "params": {"nprobe": 10}},
        "limit": top_k,
        "output_fields": output_fields,
    }
    if extra_filter:
        search_kwargs["filter"] = extra_filter
    results = get_client().search(**search_kwargs)
    search_results: list[SearchResult] = []
    if results:
        for rank, hit in enumerate(results[0]):
            chunk = _chunk_from_hit(hit)
            search_results.append(SearchResult(chunk=chunk, score=hit["distance"], rank=rank, retrieval_method="vector"))
    return search_results


def bm25_search(
    query: str,
    top_k: int | None = None,
    source_paths: list[str] | set[str] | None = None,
) -> list[SearchResult]:
    """Search using BM25 lexical matching."""
    if top_k is None:
        top_k = cfg.retrieval_top_k
    try:
        bm25, chunks = load_bm25_index()
    except FileNotFoundError:
        return []
    tokenized_query = query.lower().split()
    scores = bm25.get_scores(tokenized_query)
    allowed_paths = set(source_paths) if source_paths is not None else None
    scored = sorted(
        ((idx, score) for idx, score in enumerate(scores) if allowed_paths is None or chunks[idx].source_path in allowed_paths),
        key=lambda item: item[1],
        reverse=True,
    )[:top_k]
    return [SearchResult(chunk=chunks[idx], score=float(score), rank=rank, retrieval_method="bm25") for rank, (idx, score) in enumerate(scored)]


def _rrf_fusion(
    results_lists: list[list[SearchResult]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[SearchResult]:
    if weights is None:
        weights = [1.0] * len(results_lists)
    if len(weights) != len(results_lists):
        msg = "weights must have the same length as results_lists"
        raise ValueError(msg)
    rrf_scores: dict[str, float] = defaultdict(float)
    chunks_by_id: dict[str, Chunk] = {}
    for result_list, weight in zip(results_lists, weights, strict=True):
        for result in result_list:
            chunk_id = result.chunk.id
            rrf_scores[chunk_id] += weight / (k + result.rank + 1)
            if chunk_id not in chunks_by_id:
                chunks_by_id[chunk_id] = result.chunk
    sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
    return [SearchResult(chunk=chunks_by_id[cid], score=rrf_scores[cid], rank=rank, retrieval_method="hybrid") for rank, cid in enumerate(sorted_ids)]


def hybrid_search(
    query: str,
    query_embedding: list[float],
    top_k: int | None = None,
    extra_filter: str | None = None,
    source_paths: list[str] | set[str] | None = None,
) -> list[SearchResult]:
    """Hybrid search combining vector and BM25 results via RRF fusion."""
    if top_k is None:
        top_k = cfg.retrieval_top_k
    vector_results = vector_search(query_embedding, top_k=top_k, extra_filter=extra_filter)
    bm25_results = bm25_search(query, top_k=top_k, source_paths=source_paths)
    return _rrf_fusion([vector_results, bm25_results], weights=[cfg.hybrid_alpha, 1.0 - cfg.hybrid_alpha])


def rerank(
    query: str,
    results: list[SearchResult],
    top_k: int | None = None,
) -> list[SearchResult]:
    """Rerank search results using the configured reranker model."""
    if top_k is None:
        top_k = cfg.rerank_top_k
    if not results:
        return []
    headers = {"Authorization": f"Bearer {cfg.reranker_api_key}", "Content-Type": "application/json"}
    documents = [r.chunk.text for r in results]
    score_url = f"{cfg.reranker_base_url.rstrip('/')}/score"
    score_payload = {"model": cfg.reranker_model, "queries": [query] * len(results), "documents": documents}
    response = httpx.post(score_url, json=score_payload, headers=headers, timeout=30)
    response.raise_for_status()
    response_data = response.json()
    scored_items = [(item["index"], item.get("score", 0.0)) for item in response_data.get("data", [])]
    scored_items.sort(key=lambda x: x[1], reverse=True)
    reranked: list[SearchResult] = []
    for rank, (original_index, score) in enumerate(scored_items[:top_k]):
        original_result = results[original_index]
        reranked.append(SearchResult(chunk=original_result.chunk, score=score, rank=rank, retrieval_method="reranked"))
    return reranked
