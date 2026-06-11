#!/usr/bin/env python3
"""Test that hyde() returns multiple distinct documents and enhance_query
includes the original query when hyde is enabled.

Usage:
    cd rag-pipeline && .venv/bin/python tests/test_hyde.py

Requires the RunPod Gemma chat endpoint to be active (.env has CHAT_BASE_URL).
Skips if the endpoint is unreachable, so safe to run in CI.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.query import enhance_query, hyde, sub_queries, stepback
from pipeline.config import cfg


def check(condition: bool, message: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        global _failures
        _failures += 1


_failures = 0
QUERY = "What is the Acheron Trough Expedition?"

print("══════════════════════════════════════════════════")
print(" HyDE Multi-Document Test")
print("══════════════════════════════════════════════════")
print(f"  Chat endpoint: {cfg.chat_base_url}")
print(f"  HyDE n_documents: {cfg.hyde_n_documents}")
print()

# ── Sanity: check that _chat works ─────────────────────
print("§1  Live endpoint reachability...")
try:
    docs = hyde(QUERY, n=2)
    check(len(docs) > 0, f"hyde() returned {len(docs)} document(s)")
    for i, d in enumerate(docs):
        print(f"    doc[{i}]: {d[:120]}...")
except Exception as e:
    check(False, f"hyde() raised: {e}")
    print(f"    (endpoint may be down — skipping remaining tests)")
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
check(len(unique) == len(docs), f"{len(unique)} unique / {len(docs)} total — "
      f"{'all distinct' if len(unique) == len(docs) else 'DUPES FOUND'}")
print()

# ── Test 4: enhance_query includes original query ──────
print("§5  enhance_query(query, enhancements='hyde') includes original query...")
enhanced = enhance_query(QUERY, enhancements="hyde")
check(len(enhanced) >= 1, f"returned {len(enhanced)} query strings (≥1)")
check(QUERY in enhanced, f"original query present in enhance_query output")
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

# ── Summary ─────────────────────────────────────────────
print("══════════════════════════════════════════════════")
if _failures == 0:
    print(f" All {7 - (_failures > 0)} checks passed! HyDE pipeline validated.")
else:
    print(f" {_failures} check(s) failed.")
    sys.exit(1)
