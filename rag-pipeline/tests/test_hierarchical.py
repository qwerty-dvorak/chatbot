#!/usr/bin/env python3
"""Test hierarchical index: coarse-to-fine retrieval using summary vectors.

Tests:
  1. ingest_multiple() — ingests 3 topic files + factual_test.txt
  2. summaries stored in PostgreSQL analysis_summary
  3. summary vectors stored in Milvus (chunk_type == "summary")
  4. hierarchical search: stage 1 matches summary, stage 2 returns chunks
  5. no summary chunks in search results
  6. non-hierarchical search still works
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import db as pgdb
from pipeline.config import cfg
from pipeline.milvus import connect_milvus, get_client
from pipeline.milvus import ensure_collection as _ensure_collection
from pipeline.models import ChunkType, ContentType, RawDocument
from pipeline.process import process_text as process_document
from pipeline.search import search

BASE = Path(__file__).resolve().parent.parent.parent / "sample_data"
TOPIC_FILES = [
    BASE / "topic_geology.txt",
    BASE / "topic_biology.txt",
    BASE / "topic_astronomy.txt",
    BASE / "factual_test.txt",
]

_failures = 0


def check(condition: bool, message: str) -> None:  # noqa: FBT001
    global _failures  # noqa: PLW0603
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        _failures += 1


def clean_slate():
    # Clean PostgreSQL
    pgdb.connect()
    pgdb.execute("DELETE FROM document_chunks")
    pgdb.execute("DELETE FROM documents")
    # Drop Milvus collection
    connect_milvus()
    client = get_client()
    if client.has_collection(cfg.text_collection):
        client.drop_collection(cfg.text_collection)
    print("  [DONE] Cleaned PostgreSQL and Milvus")


def run():  # noqa: C901, PLR0915
    global _failures  # noqa: PLW0603
    _failures = 0

    print("══════════════════════════════════════════════════")
    print(" Hierarchical Index Test")
    print("══════════════════════════════════════════════════")

    # ── Setup ──────────────────────────────────────────
    print()
    print("Cleaning previous data...")
    clean_slate()

    # Save original config values
    orig_hierarchical = cfg.hierarchical_mode
    cfg.hierarchical_mode = True

    print()
    print("───────────────────────────────────────────────")
    print(" Phase 1: Ingest multiple topic documents")
    print("───────────────────────────────────────────────")

    doc_ids = []
    for fpath in TOPIC_FILES:
        fname = Path(fpath).name
        if not Path(fpath).exists():
            print(f"  [SKIP] {fpath} not found")
            continue
        print(f"  Ingesting {fname}...")
        text = Path(fpath).read_text()
        doc = RawDocument(
            path=fpath,
            content_type=ContentType.TEXT,
            text=text,
            metadata={"filename": fname, "source_path": fpath},
        )
        result = process_document(doc)
        doc_ids.append(result["document_id"])
        _summary_text = result.get("summary_text", "")
        summary_idx = result.get("summary_indexed", 0)
        print(f"    doc_id={result['document_id'][:8]}.. chunks={result['chunks_created']} summary={summary_idx} hyde={result['hyde_generated']}")

    check(len(doc_ids) >= 3, f"Ingested {len(doc_ids)} document(s)")

    # ── Phase 2: Verify summaries in PostgreSQL ────────
    print()
    print("───────────────────────────────────────────────")
    print(" Phase 2: Verify summaries in PostgreSQL")
    print("───────────────────────────────────────────────")

    rows = pgdb.execute(
        "SELECT COUNT(*) AS cnt FROM documents WHERE analysis_summary != '' AND analysis_summary IS NOT NULL",
        fetch=True,
    )
    summary_count = rows[0]["cnt"] if rows else 0
    check(summary_count >= 3, f"Summaries in PostgreSQL: {summary_count}")

    for fpath in TOPIC_FILES:
        fname = Path(fpath).name
        rows = pgdb.execute(
            "SELECT SUBSTRING(analysis_summary, 1, 100) AS s FROM documents WHERE original_filename = %s AND analysis_summary != '' LIMIT 1",
            (fname,),
            fetch=True,
        )
        if rows:
            print(f"    {fname}: {rows[0]['s']}...")

    # ── Phase 3: Verify summary vectors in Milvus ──────
    print()
    print("───────────────────────────────────────────────")
    print(" Phase 3: Verify summary vectors in Milvus")
    print("───────────────────────────────────────────────")

    client = get_client()
    _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
    smry_hits = client.query(
        collection_name=cfg.text_collection,
        filter='chunk_type == "summary"',
        output_fields=["id", "source_path", "text"],
        limit=20,
    )
    check(len(smry_hits) >= 3, f"Summary vectors in Milvus: {len(smry_hits)}")
    for hit in smry_hits:
        src = Path(hit.get("source_path", "?")).name
        txt = hit.get("text", "")[:80]
        print(f"    {src}: {txt}...")

    # ── Phase 4: Hierarchical search ──────────────────
    print()
    print("───────────────────────────────────────────────")
    print(" Phase 4: Hierarchical search")
    print("  (stage 1 = summary vectors → stage 2 = filtered chunks)")
    print("───────────────────────────────────────────────")

    # 4a: Geology query
    print()
    print("  §4a Query: 'plate tectonics subduction zones mineral deposits'")
    results = search(
        "plate tectonics subduction zones mineral deposits",
        top_k=5,
        use_reranker=True,
        retrieval_mode="hybrid",
        enhancements="",
        hierarchical=True,
    )
    check(len(results) > 0, f"Returned {len(results)} result(s)")
    no_summaries = all(r.chunk.chunk_type != ChunkType.SUMMARY for r in results)
    check(no_summaries, "No summary chunks in results")
    for r in results:
        src = Path(r.chunk.source_path).name
        print(f"    [{r.rank + 1}] score={r.score:.4f} src={src} type={r.chunk.chunk_type.value}")

    # 4b: Biology query
    print()
    print("  §4b Query: 'deep sea hydrothermal vents chemosynthesis bioluminescence'")
    results = search(
        "deep sea hydrothermal vents chemosynthesis bioluminescence",
        top_k=5,
        use_reranker=True,
        retrieval_mode="hybrid",
        enhancements="",
        hierarchical=True,
    )
    check(len(results) > 0, f"Returned {len(results)} result(s)")
    no_summaries = all(r.chunk.chunk_type != ChunkType.SUMMARY for r in results)
    check(no_summaries, "No summary chunks in results")
    for r in results:
        src = Path(r.chunk.source_path).name
        print(f"    [{r.rank + 1}] score={r.score:.4f} src={src} type={r.chunk.chunk_type.value}")

    # 4c: Astronomy query
    print()
    print("  §4c Query: 'stellar evolution supernova neutron star black hole'")
    results = search(
        "stellar evolution supernova neutron star black hole",
        top_k=5,
        use_reranker=True,
        retrieval_mode="hybrid",
        enhancements="",
        hierarchical=True,
    )
    check(len(results) > 0, f"Returned {len(results)} result(s)")
    no_summaries = all(r.chunk.chunk_type != ChunkType.SUMMARY for r in results)
    check(no_summaries, "No summary chunks in results")
    for r in results:
        src = Path(r.chunk.source_path).name
        print(f"    [{r.rank + 1}] score={r.score:.4f} src={src} type={r.chunk.chunk_type.value}")

    # ── Phase 5: Non-hierarchical search ──────────────
    print()
    print("───────────────────────────────────────────────")
    print(" Phase 5: Non-hierarchical search (standard mode)")
    print("───────────────────────────────────────────────")

    results = search(
        "Acheron Trough Expedition Nautilus VII",
        top_k=5,
        use_reranker=True,
        retrieval_mode="hybrid",
        enhancements="",
        hierarchical=False,
    )
    check(len(results) > 0, f"Returned {len(results)} result(s)")
    for r in results:
        src = Path(r.chunk.source_path).name
        print(f"    [{r.rank + 1}] score={r.score:.4f} src={src} type={r.chunk.chunk_type.value}")

    # ── Summary ────────────────────────────────────────
    cfg.hierarchical_mode = orig_hierarchical
    print()
    print("══════════════════════════════════════════════════")
    if _failures == 0:
        print(" All checks passed! Hierarchical index validated.")
    else:
        print(f" {_failures} check(s) failed.")
        sys.exit(1)


if __name__ == "__main__":
    run()
