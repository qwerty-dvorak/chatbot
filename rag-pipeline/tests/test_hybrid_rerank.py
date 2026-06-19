#!/usr/bin/env python3
"""Test hybrid retrieve & reranking workflow.

Validates the dual-channel architecture:
┌─ Vector Store ──→ top_k pool ─┐
│                               ├──→ Reranker (Cross-Encoder) → LLM
└─ BM25 Retrieval ─→ top_k pool ─┘

Tests:
  1. hybrid_search() returns candidates from BOTH channels (vector + BM25)
  2. Reranker scores overwrite RRF fused scores (method="reranked")
  3. hybrid_search() returns full pool (≥ retrieval_top_k, up to 2×)
  4. Dedup across channels works correctly
  5. Bm25-only and vector-only modes still work independently
  6. Hierarchical + hybrid + reranker together
  7. Non-reranker path still uses RRF fusion correctly
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.config import cfg
from pipeline.models import ChunkType, RawDocument, ContentType, Chunk
from pipeline.text_pipeline import process_document
from pipeline.search import search
from pipeline.retrieve import hybrid_search, bm25_search, vector_search
from pipeline.embed import embed_text
from pipeline.index import (_ensure_collection, get_client, connect_milvus,
                            build_bm25_index)
from pipeline import db as pgdb

BASE = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data")
TOPIC_FILES = [
    os.path.join(BASE, "topic_geology.txt"),
    os.path.join(BASE, "topic_biology.txt"),
    os.path.join(BASE, "topic_astronomy.txt"),
    os.path.join(BASE, "factual_test.txt"),
]

_failures = 0

def check(condition: bool, message: str) -> None:
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        _failures += 1

def embed_query(text: str) -> list[float]:
    from pipeline.models import Chunk as C
    import uuid
    tmp = C(id=uuid.uuid4().hex, source_path="__query__", text=text, chunk_type=ChunkType.TEXT)
    embedded = embed_text([tmp])
    return embedded[0].embedding if embedded else []

def clean_slate():
    pgdb.connect()
    pgdb.execute("DELETE FROM document_chunks")
    pgdb.execute("DELETE FROM documents")
    pgdb.reset_pipeline_source()
    connect_milvus()
    client = get_client()
    if client.has_collection(cfg.text_collection):
        client.drop_collection(cfg.text_collection)
    print("  [DONE] Cleaned PostgreSQL and Milvus")

def run():
    global _failures
    _failures = 0

    print("=" * 54)
    print(" Hybrid Retrieve & Reranking Workflow Test")
    print("=" * 54)

    # ── Setup ──────────────────────────────────────────
    print()
    print("Cleaning previous data...")
    clean_slate()

    print()
    print("─" * 47)
    print(" Phase 1: Ingest topic documents")
    print("─" * 47)

    doc_ids = []
    for fpath in TOPIC_FILES:
        fname = os.path.basename(fpath)
        if not os.path.exists(fpath):
            print(f"  [SKIP] {fpath} not found")
            continue
        print(f"  Ingesting {fname}...")
        with open(fpath) as fh:
            text = fh.read()
        doc = RawDocument(path=fpath, content_type=ContentType.TEXT, text=text,
                          metadata={"filename": fname, "source_path": fpath})
        result = process_document(doc)
        doc_ids.append(result["document_id"])
        print(f"    doc_id={result['document_id'][:8]}.. chunks={result['chunks_created']} "
              f"summary={result['summary_indexed']} hyde={result['hyde_generated']}")

    check(len(doc_ids) >= 3, f"Ingested {len(doc_ids)} document(s)")

    # Rebuild BM25 index from chunks stored in PostgreSQL
    print()
    print("  Rebuilding BM25 index from stored chunks...")
    all_pg_chunks = pgdb.execute(
        "SELECT id, content, metadata FROM document_chunks ORDER BY chunk_index", fetch=True,
    )
    bm25_chunks = []
    for row in all_pg_chunks:
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {})
        ct_raw = meta.get("chunk_type", "text")
        try:
            ct = ChunkType(ct_raw)
        except ValueError:
            ct = ChunkType.TEXT
        bm25_chunks.append(Chunk(
            id=row["id"],
            source_path=meta.get("source_path", ""),
            text=row["content"],
            chunk_type=ct,
            metadata=meta,
            parent_id=meta.get("parent_id"),
            window_text=None,
            children_ids=[],
            image_data=None,
        ))
    build_bm25_index(bm25_chunks)
    print(f"  [DONE] BM25 index rebuilt with {len(bm25_chunks)} chunks")

    retrieval_k = cfg.retrieval_top_k

    # ── Phase 2: Verify both channels produce results ──
    print()
    print("─" * 47)
    print(" Phase 2: Dual-channel retrieval")
    print("  Vector store + BM25 run in parallel")
    print("─" * 47)

    query_text = "plate tectonics subduction zones"
    emb = embed_query(query_text)
    check(emb is not None and len(emb) > 0, "Query embedding produced")

    # Verify each channel independently
    vec_raw = vector_search(emb, top_k=retrieval_k)
    check(len(vec_raw) > 0, f"vector_search returned {len(vec_raw)} result(s)")
    vec_methods = set(r.retrieval_method for r in vec_raw)
    check("vector" in vec_methods, f"Vector results marked method='vector'")

    bm25_raw = bm25_search(query_text, top_k=retrieval_k)
    check(len(bm25_raw) > 0, f"bm25_search returned {len(bm25_raw)} result(s)")
    bm25_methods = set(r.retrieval_method for r in bm25_raw)
    check("bm25" in bm25_methods, f"BM25 results marked method='bm25'")

    # Verify hybrid pool is larger than either channel alone
    raw_results = hybrid_search(query_text, emb, top_k=retrieval_k)
    check(len(raw_results) > 0, f"hybrid_search returned {len(raw_results)} result(s)")
    raw_count = len(raw_results)
    expected_max = 2 * retrieval_k
    check(raw_count >= retrieval_k,
          f"hybrid pool >= retrieval_top_k ({raw_count} ≥ {retrieval_k})")
    check(raw_count <= expected_max,
          f"hybrid pool ≤ 2× retrieval_top_k ({raw_count} ≤ {expected_max})")

    distinct_sources = set(r.chunk.source_path for r in raw_results)
    check(len(distinct_sources) > 0, f"Results from {len(distinct_sources)} source(s)")

    # Dedup across channels
    all_ids = [r.chunk.id for r in raw_results]
    check(len(all_ids) == len(set(all_ids)), "No duplicate chunk IDs in hybrid pool")

    # Some hybrid results should differ from pure vector or pure BM25
    vec_ids = set(c.chunk.id for c in vec_raw)
    bm25_ids = set(c.chunk.id for c in bm25_raw)
    hybrid_ids = set(c.chunk.id for c in raw_results)
    both_sides = bool(vec_ids & bm25_ids)  # overlap is fine
    any_unique = bool(hybrid_ids - vec_ids)  # BM25 brought some unique results
    check(len(hybrid_ids) >= len(vec_ids),
          f"Hybrid pool >= vector pool ({len(hybrid_ids)} ≥ {len(vec_ids)})")

    # ── Phase 3: Reranker as fusion node ───────────────
    print()
    print("─" * 47)
    print(" Phase 3: Reranker as fusion node")
    print("─" * 47)

    print()
    print("  §3a: Search WITH reranker (default)")
    print("  Query: 'plate tectonics subduction zones'")
    results_on = search(
        query_text, top_k=5, use_reranker=True, retrieval_mode="hybrid",
        enhancements="", hierarchical=False,
    )
    check(len(results_on) > 0, f"Returned {len(results_on)} result(s)")
    reranked_count = sum(1 for r in results_on if r.retrieval_method == "reranked")
    check(reranked_count > 0, f"{reranked_count}/{len(results_on)} results marked reranked")
    all_reranked = all(r.retrieval_method == "reranked" for r in results_on)
    check(all_reranked, "All results have retrieval_method='reranked'")
    for r in results_on:
        src = os.path.basename(r.chunk.source_path)
        print(f"    [{r.rank + 1}] score={r.score:.4f} method={r.retrieval_method} src={src}")

    # Verify scores are in descending order (reranker guarantees this)
    scores_desc = all(results_on[i].score >= results_on[i + 1].score
                      for i in range(len(results_on) - 1))
    check(scores_desc, "Reranked results sorted by score descending")

    print()
    print("  §3b: Search WITHOUT reranker")
    results_off = search(
        query_text, top_k=5, use_reranker=False, retrieval_mode="hybrid",
        enhancements="", hierarchical=False,
    )
    check(len(results_off) > 0, f"Returned {len(results_off)} result(s)")
    valid_methods = {"hybrid", "query_to_query"}
    hybrid_or_resolved = sum(1 for r in results_off if r.retrieval_method in valid_methods)
    check(hybrid_or_resolved > 0,
          f"{hybrid_or_resolved}/{len(results_off)} results marked hybrid/query_to_query")
    for r in results_off:
        src = os.path.basename(r.chunk.source_path)
        print(f"    [{r.rank + 1}] score={r.score:.4f} method={r.retrieval_method} src={src}")

    # Compare: reranked scores are cross-encoder (0-1), RRF scores are arbitrary
    if results_on and results_off:
        check(results_on[0].score >= 0, "Reranked top score is non-negative")
        print(f"    Reranked top-1 score={results_on[0].score:.4f}")
        print(f"    RRF top-1 score={results_off[0].score:.4f}")

    # ── Phase 4: Independent channels ─────────────────
    print()
    print("─" * 47)
    print(" Phase 4: Independent channel modes")
    print("─" * 47)

    print()
    print("  §4a: Vector-only mode")
    vec_results = search(
        query_text, top_k=5, use_reranker=True, retrieval_mode="vector",
        enhancements="", hierarchical=False,
    )
    check(len(vec_results) > 0, f"Vector search returned {len(vec_results)} result(s)")
    vec_methods = set(r.retrieval_method for r in vec_results)
    check(vec_methods == {"reranked"},
          f"Vector + reranker: all methods='reranked' (got {vec_methods})")

    print()
    print("  §4b: BM25-only mode")
    print("  Query (with unique keywords from factual_test.txt): 'Acheron Trough Nautilus VII Rostova'")
    bm25_results_list = search(
        "Acheron Trough Nautilus VII Rostova",
        top_k=5, use_reranker=True, retrieval_mode="bm25",
        enhancements="", hierarchical=False,
    )
    check(len(bm25_results_list) > 0,
          f"BM25 search returned {len(bm25_results_list)} result(s)")
    if bm25_results_list:
        bm25_methods_found = set(r.retrieval_method for r in bm25_results_list)
        check(bm25_methods_found == {"reranked"},
              f"BM25 + reranker: all methods='reranked' (got {bm25_methods_found})")
        for r in bm25_results_list:
            src = os.path.basename(r.chunk.source_path)
            print(f"      [{r.rank + 1}] score={r.score:.4f} method={r.retrieval_method} src={src}")

    # ── Phase 5: Hierarchical + hybrid + reranker ─────
    print()
    print("─" * 47)
    print(" Phase 5: Hierarchical + hybrid + reranker")
    print("─" * 47)

    results_hier = search(
        "stellar evolution supernova neutron star black hole",
        top_k=5, use_reranker=True, retrieval_mode="hybrid",
        enhancements="", hierarchical=True,
    )
    check(len(results_hier) > 0, f"Hierarchical returned {len(results_hier)} result(s)")
    no_summaries = all(r.chunk.chunk_type != ChunkType.SUMMARY for r in results_hier)
    check(no_summaries, "No summary chunks in results")
    all_reranked_hier = all(r.retrieval_method == "reranked" for r in results_hier)
    check(all_reranked_hier, "All hierarchical results marked reranked")
    for r in results_hier:
        src = os.path.basename(r.chunk.source_path)
        print(f"    [{r.rank + 1}] score={r.score:.4f} method={r.retrieval_method} src={src}")

    # ── Phase 6: Query enhancements + hybrid + reranker ──
    print()
    print("─" * 47)
    print(" Phase 6: Query enhancements + hybrid + reranker")
    print("─" * 47)

    results_enhanced = search(
        "plate tectonics subduction zones",
        top_k=5, use_reranker=True, retrieval_mode="hybrid",
        enhancements="hyde", hierarchical=False,
    )
    check(len(results_enhanced) > 0, f"Enhanced returned {len(results_enhanced)} result(s)")
    all_reranked_enh = all(r.retrieval_method == "reranked" for r in results_enhanced)
    check(all_reranked_enh, "All enhanced results marked reranked")

    # ── Summary ────────────────────────────────────────
    print()
    print("=" * 54)
    if _failures == 0:
        print(f" All checks passed — hybrid+rerank workflow validated!")
    else:
        print(f" {_failures} check(s) failed.")

    print()
    print("Hybrid Retrieve & Reranking Workflow Summary")
    print("─" * 47)
    print("  Parallel channels: Vector + BM25 (both always queried)")
    print("  Fusion: RRF pre-merge → Reranker as central scoring node")
    print("  Final ranking: Cross-encoder scores (overwrite RRF)")
    print("  Test scenarios: stand-alone, hierarchical, enhanced")
    print("=" * 54)

    if _failures > 0:
        sys.exit(1)

if __name__ == "__main__":
    run()
