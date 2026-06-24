#!/bin/bash
# Test: knowledge.grep tool correctly finds exact substrings in uploaded documents.
# Does NOT call the LLM — invokes the tool handler directly.
#
# Prerequisites:
#   - book_with_needle.txt must already be ingested (chunks exist in DB)
#   - CONTAINER must be running
#
# Usage:
#   bash tests/chatbot/test_needle_grep.sh
#   CONTAINER=web SETTINGS=config.settings.production bash tests/chatbot/test_needle_grep.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

CONTAINER="${CONTAINER:-web}"
SETTINGS="${SETTINGS:-config.settings.production}"

docker exec "$CONTAINER" uv run python -c """
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', '$SETTINGS')
import django; django.setup()

from apps.tools.builtin import knowledge_grep
from apps.knowledge.models import DocumentChunk

# ── ensure book_with_needle.txt chunks exist ──────────────────────────
doc_title = 'book_with_needle.txt'
chunk_count = DocumentChunk.objects.filter(document__title=doc_title).count()
print(f'Chunks for \"{doc_title}\": {chunk_count}')
if chunk_count == 0:
    print('FAIL: no chunks found — ingest the document first')
    exit(1)

# ── test: grep for AlphaSecret999 ──────────────────────────────────────
result = knowledge_grep({'pattern': 'AlphaSecret999', 'top_k': 5}, {})
count = result.get('count', 0)
matches = result.get('matches', [])
print(f'knowledge.grep(\"AlphaSecret999\") → {count} match(es)')

if count == 0:
    print('FAIL: expected at least 1 match for AlphaSecret999')
    exit(1)

# Verify the match comes from the right document and contains the needle
found = False
for m in matches:
    if doc_title in m.get('document_title', '') and 'AlphaSecret999' in m.get('snippet', ''):
        found = True
        print(f'  ✓ doc={m[\"document_title\"]} chunk={m[\"chunk_index\"]}')
        print(f'    snippet contains AlphaSecret999')
        break

if not found:
    print('FAIL: no match with AlphaSecret999 in snippet from book_with_needle.txt')
    exit(1)

# ── test: grep for OmegaSecret999 ──────────────────────────────────────
result2 = knowledge_grep({'pattern': 'OmegaSecret999', 'top_k': 5}, {})
count2 = result2.get('count', 0)
print(f'knowledge.grep(\"OmegaSecret999\") → {count2} match(es)')
if count2 == 0:
    print('FAIL: expected at least 1 match for OmegaSecret999')
    exit(1)

print()
print('PASS: all knowledge.grep tests passed')
"""
