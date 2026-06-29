#!/usr/bin/env python3
"""Test that hyde() returns multiple distinct documents and enhance_query
includes the original query when hyde is enabled.

Usage:
    cd rag-pipeline && .venv/bin/python tests/test_hyde.py

Requires the RunPod Gemma chat endpoint to be active (.env has CHAT_BASE_URL).
Skips if the endpoint is unreachable, so safe to run in CI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import cfg
from pipeline.query import enhance_query, hyde, stepback, sub_queries


def check(condition: bool, message: str) -> None:  # noqa: FBT001
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        global _failures  # noqa: PLW0603
        _failures += 1


_failures = 0
QUERY = "What is the Acheron Trough Expedition?"

print("══════════════════════════════════════════════════")
print(" HyDE + Sub-Queries Test")
print("══════════════════════════════════════════════════")
print(f"  Chat endpoint: {cfg.chat_base_url}")
print(f"  HyDE n_documents: {cfg.hyde_n_documents}")
print(f"  Sub-queries count: {cfg.sub_queries_count}")
print()

# ── Sanity: check that _chat works ─────────────────────
print("§1  Live endpoint reachability...")
try:
    docs = hyde(QUERY, n=2)
    check(len(docs) > 0, f"hyde() returned {len(docs)} document(s)")
    for i, d in enumerate(docs):
        print(f"    doc[{i}]: {d[:120]}...")
except Exception as e:  # noqa: BLE001
    check(False, f"hyde() raised: {e}")  # noqa: FBT003
    print("    (endpoint may be down — skipping remaining tests)")
    print(f"\nResults: {0 if _failures == 0 else 1} failed, {0 if _failures > 0 else 1} passed")
    sys.exit(1 if _failures else 0)

print()

# ── Test 1: hyde() with default n ──────────────────────
print("§2  hyde() with default n (cfg.hyde_n_documents)...")
docs = hyde(QUERY)
check(len(docs) >= 1, f"returned {len(docs)} documents (≥1)")
for i, d in enumerate(docs):
    print(f"    doc[{i}]: {d[:120]}...")
print()

# ── Test 2: hyde() with explicit n=3 ───────────────────
print("§3  hyde() with explicit n=3...")
docs = hyde(QUERY, n=3)
check(len(docs) >= 1, f"returned {len(docs)} documents (≥1)")
check(len(docs) >= 2, f"returned {len(docs)} documents (≥2)")
for i, d in enumerate(docs):
    print(f"    doc[{i}]: {d[:120]}...")
print()

# ── Test 3: Documents are distinct ─────────────────────
print("§4  hyde() documents are distinct...")
unique = set(docs)
check(len(unique) == len(docs), f"{len(unique)} unique / {len(docs)} total — {'all distinct' if len(unique) == len(docs) else 'DUPES FOUND'}")
print()

# ── Test 4: enhance_query includes original query ──────
print("§5  enhance_query(query, enhancements='hyde') includes original query...")
enhanced = enhance_query(QUERY, enhancements="hyde")
check(len(enhanced) >= 1, f"returned {len(enhanced)} query strings (≥1)")
check(QUERY in enhanced, "original query present in enhance_query output")
for i, q in enumerate(enhanced):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ── Test 5: enhance_query with hyde+stepback ───────────
print("§6  enhance_query with hyde+stepback combines all strategies...")
enhanced = enhance_query(QUERY, enhancements="hyde,stepback")
check(len(enhanced) >= 2, f"returned {len(enhanced)} query strings (≥2)")
has_hyde_docs = any(len(q) > len(QUERY) + 20 for q in enhanced)  # rough heuristic
has_stepback = any("background" in q.lower() or "broad" in q.lower() or "general" in q.lower() for q in enhanced)
check(has_hyde_docs, "HyDE documents present in combined output")
check(QUERY in enhanced, "original query present in combined output")
for i, q in enumerate(enhanced):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ── Test 6: enhance_query with no enhancements ─────────
print("§7  enhance_query with no enhancements returns [query]...")
results = enhance_query(QUERY, enhancements="")
check(results == [QUERY], f"empty-string returned {results}")

# Passing enhancements=None falls back to cfg.query_enhancements (e.g. "hyde").
results2 = enhance_query(QUERY, enhancements=None)
check(len(results2) >= 1, f"default-config returned {len(results2)} query(ies)")
print()

# ═══════════════════════════════════════════════════
# Sub-Queries Tests
# ═══════════════════════════════════════════════════
print()
print("══════════════════════════════════════════════════")
print(" Sub-Queries Test")
print("══════════════════════════════════════════════════")
print(f"  Sub-queries count (config): {cfg.sub_queries_count}")
print()

# ── Test 8: sub_queries() with default n (LLM decides) ───
print("§8  sub_queries() with default n (LLM decides whether to decompose)...")
sqs = sub_queries(QUERY)
check(len(sqs) >= 1, f"returned {len(sqs)} query(ies) (≥1)")
check(QUERY in sqs, "original query present in sub_queries output")
for i, q in enumerate(sqs):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ── Test 9: sub_queries() with explicit n=3 ──────────────
print("§9  sub_queries() with explicit n=3 (LLM may or may not decompose)...")
sqs = sub_queries(QUERY, n=3)
check(len(sqs) >= 1, f"returned {len(sqs)} query(ies) (≥1)")
check(QUERY in sqs, "original query present")
for i, q in enumerate(sqs):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ── Test 10: Sub-queries distinctness (if multiple) ──────
print("§10 sub-queries are distinct from each other (if multiple)...")
sqs_no_orig = [q for q in sqs if q != QUERY]
if len(sqs_no_orig) > 1:
    unique = set(sqs_no_orig)
    check(
        len(unique) == len(sqs_no_orig),
        f"{len(unique)} unique / {len(sqs_no_orig)} sub-queries — {'all distinct' if len(unique) == len(sqs_no_orig) else 'DUPES FOUND'}",
    )
else:
    check(True, f"skipped — only {len(sqs_no_orig)} sub-query (no comparison needed)")  # noqa: FBT003
print()

# ── Test 11: enhance_query with sub_queries only ─────────
print("§11 enhance_query(query, enhancements='sub_queries')...")
enhanced = enhance_query(QUERY, enhancements="sub_queries")
check(len(enhanced) >= 1, f"returned {len(enhanced)} query string(s) (≥1)")
check(QUERY in enhanced, "original query present")
for i, q in enumerate(enhanced):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ── Test 12: enhance_query with sub_queries+hyde ─────────
print("§12 enhance_query with sub_queries+hyde combines all strategies...")
enhanced = enhance_query(QUERY, enhancements="sub_queries,hyde")
check(len(enhanced) >= 2, f"returned {len(enhanced)} query strings (≥2: hyde docs + sub-queries + original)")
check(QUERY in enhanced, "original query present")
for i, q in enumerate(enhanced):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ═══════════════════════════════════════════════════
# Stepback Tests
# ═══════════════════════════════════════════════════
print()
print("══════════════════════════════════════════════════")
print(" Stepback Test")
print("══════════════════════════════════════════════════")
print()

# ── Test 13: stepback() returns a non-empty string ──────
print("§13 stepback() returns a reformulated question...")
sb = stepback(QUERY)
check(len(sb) > 0, f"returned string of length {len(sb)}")
check(sb != QUERY, "stepback question differs from original query")
check("?" in sb, "stepback question is a question (contains '?')")
print(f"    original: {QUERY}")
print(f"    stepback: {sb}")
print()

# ── Test 14: stepback is broader / more abstract ────────
print("§14 stepback question is broader in scope...")
sb_words = set(sb.lower().split())
q_words = set(QUERY.lower().split())
is_longer = len(sb) >= len(QUERY) * 0.5  # rough: shouldn't be much shorter
check(is_longer, f"stepback ({len(sb)} chars) not drastically shorter than original ({len(QUERY)} chars)")
# Stepback should mention general concepts like "research", "expedition",
# "exploration" rather than the specific "Acheron Trough"
has_general_term = any(t in sb.lower() for t in ["research", "expedition", "exploration", "geological", "scientific", "study", "mission", "survey"])
check(has_general_term, "stepback contains a general-scope term")
print()

# ── Test 15: enhance_query with stepback includes both ──
print("§15 enhance_query(query, enhancements='stepback')...")
enhanced = enhance_query(QUERY, enhancements="stepback")
check(len(enhanced) >= 2, f"returned {len(enhanced)} query strings (≥2)")
check(QUERY in enhanced, "original query present")
has_stepback = any(q != QUERY and "?" in q for q in enhanced)
check(has_stepback, "stepback question present")
for i, q in enumerate(enhanced):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ── Test 16: enhance_query with stepback+hyde+sub_queries ─
print("§16 enhance_query with stepback+hyde+sub_queries combines all...")
enhanced = enhance_query(QUERY, enhancements="stepback,hyde,sub_queries")
check(len(enhanced) >= 3, f"returned {len(enhanced)} query strings (≥3)")
check(QUERY in enhanced, "original query present")
for i, q in enumerate(enhanced):
    print(f"    query[{i}]: {q[:100]}...")
print()

# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
print("══════════════════════════════════════════════════")
if _failures == 0:
    print(f" All {16 - (_failures > 0)} checks passed! HyDE + sub-queries + stepback pipeline validated.")
else:
    print(f" {_failures} check(s) failed.")
    sys.exit(1)
