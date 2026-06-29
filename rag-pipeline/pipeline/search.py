"""Search orchestrator for the RAG pipeline."""

import re
import time
import uuid

from .config import cfg
from .embed import embed_text
from .index import _chunk_from_hit, _ensure_collection, connect_milvus, get_client
from .models import Chunk, ChunkType, SearchResult
from .query import enhance_query
from .retrieve import (
    _rrf_fusion,
    bm25_search,
    hybrid_search,
    rerank,
    scoped_lexical_search,
    vector_search,
)


def _paths_filter(paths: list[str]) -> str:
    escaped = [path.replace("\\", "\\\\").replace('"', '\\"') for path in paths]
    return "source_path in [" + ", ".join(f'"{path}"' for path in escaped) + "]"


def _artifact_paths(artifact_sources: list[str], timing: dict[str, float | str]) -> list[str]:
    t0 = time.time()
    _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
    hits = get_client().query(
        collection_name=cfg.text_collection,
        output_fields=["source_path"],
        limit=10000,
    )
    requested_names = {source.casefold() for source in artifact_sources}
    paths = list({
        hit["source_path"]
        for hit in hits
        if hit.get("source_path", "").replace("\\", "/").rsplit("/", 1)[-1].casefold()
        in requested_names
    })
    timing["artifact_filter"] = round(time.time() - t0, 4)
    return paths


def _resolve_hypothetical_hit(result: SearchResult) -> SearchResult:
    chunk = result.chunk
    is_hypothetical = (
        chunk.chunk_type == ChunkType.HYPOTHETICAL_QUESTION
        or chunk.metadata.get("is_hypothetical_question") is True
    )
    if not is_hypothetical or not chunk.parent_id:
        return result

    try:
        _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
        hits = list(get_client().query(
            collection_name=cfg.text_collection,
            filter=f'id == "{chunk.parent_id}"',
            output_fields=[
                "id", "text", "source_path", "chunk_type",
                "parent_id", "window_text", "metadata_json",
            ],
            limit=1,
        ))
    except Exception:
        return result

    if hits:
        result.chunk = _chunk_from_hit(hits[0])
        result.retrieval_method = "query_to_query"
    return result


def search(
    query: str,
    top_k: int | None = None,
    use_reranker: bool = True,
    retrieval_mode: str = "hybrid",
    enhancements: str | list[str] | tuple[str, ...] | None = None,
    hierarchical: bool | None = None,
    artifact_sources: list[str] | None = None,
    pre_enhanced_queries: list[str] | None = None,
) -> tuple[list[SearchResult], dict[str, float | str]]:
    """Run query enhancement, retrieval, fusion, optional reranking, and context fetch."""
    timing: dict[str, float | str] = {}
    clock = time.time

    connect_milvus()

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
        return [], {
            "empty_store": 0.001,
            "total": 0.001,
            "message": "Vector store is empty - ingest documents first",
        }

    effective_top_k = top_k if top_k is not None else cfg.rerank_top_k
    retrieval_k = cfg.retrieval_top_k
    mode = retrieval_mode.lower()
    if mode not in {"vector", "bm25", "hybrid"}:
        raise ValueError("retrieval_mode must be one of: vector, bm25, hybrid")
    if hierarchical is None:
        hierarchical = cfg.hierarchical_mode

    artifact_paths: list[str] | None = None
    if artifact_sources:
        artifact_paths = _artifact_paths(artifact_sources, timing)
        if not artifact_paths:
            timing["total"] = round(sum(v for v in timing.values() if isinstance(v, float)), 4)
            return [], timing

    if pre_enhanced_queries is None:
        t0 = clock()
        enhanced_queries = enhance_query(query, enhancements=enhancements)
        timing["enhance_query"] = round(clock() - t0, 4)
    else:
        enhanced_queries = pre_enhanced_queries
        timing["enhance_query"] = 0.0

    lexical_results: list[SearchResult] = []
    if artifact_paths:
        t0 = clock()
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
        timing["scoped_lexical"] = round(clock() - t0, 4)

    t_embed_total = 0.0
    t_search_total = 0.0
    all_result_lists: list[list[SearchResult]] = []

    for query_text in enhanced_queries:
        query_chunk = Chunk(
            id=uuid.uuid4().hex,
            source_path="__query__",
            text=query_text,
            chunk_type=ChunkType.TEXT,
        )
        t0 = clock()
        embedded = embed_text([query_chunk])
        t_embed_total += clock() - t0
        if not embedded:
            continue
        embedding = embedded[0].embedding

        artifact_filter = _paths_filter(artifact_paths) if artifact_paths else None
        t0 = clock()
        if hierarchical and artifact_filter is None:
            summary_hits = vector_search(
                embedding,
                top_k=cfg.summary_top_k,
                extra_filter='chunk_type == "summary"',
            )
            matched_paths = list({hit.chunk.source_path for hit in summary_hits})
            if matched_paths:
                extra = f"{_paths_filter(matched_paths)} and chunk_type != \"summary\""
                if mode == "vector":
                    results = vector_search(embedding, retrieval_k, extra_filter=extra)
                elif mode == "bm25":
                    results = bm25_search(query_text, retrieval_k, source_paths=matched_paths)
                else:
                    results = hybrid_search(
                        query_text,
                        embedding,
                        retrieval_k,
                        extra_filter=extra,
                        source_paths=matched_paths,
                    )
            else:
                results = []
        elif mode == "vector":
            results = vector_search(embedding, retrieval_k, extra_filter=artifact_filter)
        elif mode == "bm25":
            results = bm25_search(query_text, retrieval_k, source_paths=artifact_paths)
        else:
            results = hybrid_search(
                query_text,
                embedding,
                retrieval_k,
                extra_filter=artifact_filter,
                source_paths=artifact_paths,
            )
        t_search_total += clock() - t0

        if results:
            all_result_lists.append(results)

    timing["embed"] = round(t_embed_total, 4)
    timing["search"] = round(t_search_total, 4)

    if not all_result_lists:
        timing["total"] = round(sum(v for v in timing.values() if isinstance(v, float)), 4)
        return lexical_results[:effective_top_k], timing

    t0 = clock()
    merged = _rrf_fusion(all_result_lists)
    timing["fusion"] = round(clock() - t0, 4)

    t0 = clock()
    seen: dict[str, SearchResult] = {}
    for result in merged:
        result = _resolve_hypothetical_hit(result)
        chunk_id = result.chunk.id
        if chunk_id not in seen or result.score > seen[chunk_id].score:
            seen[chunk_id] = result
    deduped = sorted(seen.values(), key=lambda item: item.score, reverse=True)
    for rank, result in enumerate(deduped):
        result.rank = rank
    timing["dedup"] = round(clock() - t0, 4)

    if use_reranker and deduped:
        t0 = clock()
        deduped = rerank(query, deduped, top_k=effective_top_k)
        timing["rerank"] = round(clock() - t0, 4)

    t0 = clock()
    for result in deduped:
        chunk = result.chunk
        if chunk.chunk_type != ChunkType.CHILD or not chunk.parent_id or chunk.window_text:
            continue
        try:
            _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
            hits = list(get_client().query(
                collection_name=cfg.text_collection,
                filter=f'id == "{chunk.parent_id}"',
                output_fields=[
                    "id", "text", "source_path", "chunk_type",
                    "parent_id", "window_text", "metadata_json",
                ],
                limit=1,
            ))
            if hits and hits[0].get("text"):
                chunk.window_text = hits[0]["text"]
        except Exception:
            pass
    timing["parent_fetch"] = round(clock() - t0, 4)

    if lexical_results:
        lexical_ids = {result.chunk.id for result in lexical_results}
        deduped = lexical_results + [
            result for result in deduped if result.chunk.id not in lexical_ids
        ]
        for rank, result in enumerate(deduped):
            result.rank = rank

    timing["total"] = round(sum(v for v in timing.values() if isinstance(v, float)), 4)
    return deduped[:effective_top_k], timing


def format_results(results: list[SearchResult]) -> str:
    """Format search results for CLI display."""
    if not results:
        return "No results found."

    lines: list[str] = []
    separator = "-" * 60
    for result in results:
        chunk = result.chunk
        display_text = (chunk.window_text or chunk.text).strip()
        if len(display_text) > 500:
            display_text = display_text[:500] + "..."

        lines.append(separator)
        lines.append(
            f"[{result.rank + 1}] score={result.score:.4f}  method={result.retrieval_method}"
        )
        lines.append(f"    source: {chunk.source_path}")
        lines.append(f"    type:   {chunk.chunk_type.value}")
        if chunk.parent_id:
            lines.append(f"    parent: {chunk.parent_id}")
        lines.append("")
        lines.append(display_text)

    lines.append(separator)
    return "\n".join(lines)
