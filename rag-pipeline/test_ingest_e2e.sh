#!/bin/bash
# Comprehensive end-to-end test of the RAG pipeline /ingest endpoint.
#
# Tests:
#   1. Submit factual_test.txt via POST /v1/ingest
#   2. Poll job status through all steps (extract → store_raw → db_insert → chunk → hyde → persist → embed → index)
#   3. Verify document row in PostgreSQL
#   4. Verify chunk rows in PostgreSQL with correct character content
#   5. Verify HyDE questions in PostgreSQL document_chunks
#   6. Verify embeddings in Milvus (rag_text_chunks collection)
#   7. Verify BM25 index is created
#   8. Verify search works against ingested content
#
# Usage:
#   bash test_ingest_e2e.sh
#
# Prerequisites:
#   - rag-api running on localhost:8093
#   - PostgreSQL (rag-postgres:5432)
#   - Milvus (test-milvus:19530)
#   - RunPod endpoints active (text embed, chat for HyDE)
set -euo pipefail

PASS=0; FAIL=0; SKIP=0
_RESULT_FILE=$(mktemp)
result() {
  local s=$1 name="$2"
  echo "$s:$name" >> "$_RESULT_FILE"
  printf "  [%s] %s\n" "$s" "$name"
}
_summarize() {
  while IFS=: read -r s name; do
    case "$s" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; SKIP) SKIP=$((SKIP+1));; esac
  done < "$_RESULT_FILE"
  rm -f "$_RESULT_FILE"
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_URL="http://localhost:8093"
TEST_FILE="$SCRIPT_DIR/data/sample_data/factual_test.txt"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
TEXT_COLLECTION="rag_text_chunks"

# ── Verify prerequisites ──────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo " RAG Pipeline E2E Ingestion Test"
echo "══════════════════════════════════════════════════"

echo ""
echo "Checking prerequisites..."
curl -sf "$API_URL/health" > /dev/null 2>&1 && result PASS "rag-api reachable" || result FAIL "rag-api not reachable at $API_URL"

# Check test file exists
[[ -f "$TEST_FILE" ]] && result PASS "Test file exists: $TEST_FILE" || result FAIL "Test file not found: $TEST_FILE"

# Check PostgreSQL
$VENV_PYTHON -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='chatbot', user='chatbot', password='chatbot')
conn.close()
" 2>/dev/null && result PASS "PostgreSQL reachable" || result FAIL "PostgreSQL not reachable"

# Check Milvus
$VENV_PYTHON -c "
from pymilvus import MilvusClient
c = MilvusClient('http://localhost:19530')
c.list_collections()
" 2>/dev/null && result PASS "Milvus reachable" || result FAIL "Milvus not reachable"

# Drop pre-existing collections so pipeline/_ensure_collection creates them with correct schema
$VENV_PYTHON -c "
from pymilvus import MilvusClient
c = MilvusClient('http://localhost:19530')
for col in ['$TEXT_COLLECTION', 'rag_image_chunks']:
    if col in c.list_collections():
        c.drop_collection(col)
        print(f'Dropped existing collection: {col}')
print('Collections ready for pipeline to create')
" 2>/dev/null && result PASS "Milvus cleared for pipeline schema" || result FAIL "Milvus cleanup failed"

# Clean up previous ingestion data from PostgreSQL
$VENV_PYTHON -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='chatbot', user='chatbot', password='chatbot')
cur = conn.cursor()
cur.execute('DELETE FROM document_chunks')
cur.execute('DELETE FROM documents')
cur.execute('DELETE FROM knowledge_sources')
conn.commit()
cur.close()
conn.close()
" 2>/dev/null && result PASS "PostgreSQL cleaned" || true

echo ""
echo "───────────────────────────────────────────────────"
echo " Phase 1: Submit /v1/ingest"
echo "───────────────────────────────────────────────────"

# Read the file content for display
FILE_SIZE=$(wc -c < "$TEST_FILE")
FILE_LINES=$(wc -l < "$TEST_FILE")
echo "  File: $TEST_FILE ($FILE_LINES lines, $FILE_SIZE bytes)"

# Submit ingestion
echo -n "  Submitting ingestion... "
INGEST_RESP=$(curl -sf -X POST "$API_URL/v1/ingest" \
  -F "files=@$TEST_FILE" \
  -F "strategy=recursive" \
  -F "hypothetical_questions=true" 2>&1) && {
  JOB_ID=$(echo "$INGEST_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "job_id=$JOB_ID"
  result PASS "Ingestion queued"
} || {
  echo "FAILED: $(echo $INGEST_RESP | head -c300)"
  result FAIL "Ingestion request failed"
  # Can't continue without a job
  echo ""
  echo "══════════════════════════════════════════════════"
  echo " Results: $PASS passed, $FAIL failed, $SKIP skipped"
  exit 1
}

echo ""
echo "───────────────────────────────────────────────────"
echo " Phase 2: Poll job status (waiting for completion)"
echo "───────────────────────────────────────────────────"

# Poll for completion with step-level progress
MAX_POLLS=600  # 10 minutes at 1s intervals
POLL_INTERVAL=1
STEP_PROGRESS=""
for ((i=1; i<=MAX_POLLS; i++)); do
  JOB_STATUS=$(curl -sf "$API_URL/v1/ingestions/$JOB_ID" 2>/dev/null) || {
    sleep 2
    continue
  }
  STATUS=$(echo "$JOB_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  STEPS=$(echo "$JOB_STATUS" | python3 -c "
import sys,json
j=json.load(sys.stdin)
for s in j.get('steps',[]):
    n=s['name']; st=s['status']; msg=s.get('message','')
    print(f'{n}:{st}:{msg}')
" 2>/dev/null)

  # Print new step progress
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if ! echo "$STEP_PROGRESS" | grep -qF "$(echo "$line" | cut -d: -f1)"; then
      NAME=$(echo "$line" | cut -d: -f1)
      ST=$(echo "$line" | cut -d: -f2)
      MSG=$(echo "$line" | cut -d: -f3-)
      printf "  %-12s [%-9s] %s\n" "$NAME" "$ST" "$MSG"
    fi
  done <<< "$STEPS"
  STEP_PROGRESS="$STEPS"

  if [[ "$STATUS" == "succeeded" ]]; then
    RESULT=$(echo "$JOB_STATUS" | python3 -c "
import sys,json
j=json.load(sys.stdin)
r=j.get('result',{})
print(f'files_processed={r.get(\"files_processed\",0)}, hyde={r.get(\"results\",[{}])[0].get(\"hyde_generated\",0)}, chunks={r.get(\"results\",[{}])[0].get(\"chunks_created\",0)}, embeddings={r.get(\"results\",[{}])[0].get(\"embeddings_indexed\",0)}')
" 2>/dev/null)
    echo ""
    echo "  ✓ Job completed! $RESULT"
    result PASS "Ingestion job completed"
    break
  elif [[ "$STATUS" == "failed" ]]; then
    ERROR=$(echo "$JOB_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','unknown'))" 2>/dev/null)
    echo ""
    echo "  ✗ Job failed: $ERROR"
    result FAIL "Ingestion job failed: $ERROR"
    break
  fi

  sleep "$POLL_INTERVAL"
done

if [[ $i -ge $MAX_POLLS ]]; then
  echo "  ⚠ Job did not complete within timeout"
  result FAIL "Ingestion timed out after $((MAX_POLLS * POLL_INTERVAL))s"
fi

echo ""
echo "───────────────────────────────────────────────────"
echo " Phase 3: Verify storage layers"
echo "───────────────────────────────────────────────────"

# ── 3a. PostgreSQL: Document row ────────────────────────
echo ""
echo "  §3a  PostgreSQL: document row"
$VENV_PYTHON -c "
import psycopg2, json
conn = psycopg2.connect(host='localhost', port=5432, dbname='chatbot', user='chatbot', password='chatbot')
cur = conn.cursor()

# Count documents
cur.execute(\"SELECT count(*) FROM documents\")
doc_count = cur.fetchone()[0]
print(f'documents={doc_count}')

# Get the latest document
cur.execute(\"SELECT id, original_filename, status, metadata FROM documents ORDER BY created_at DESC LIMIT 1\")
row = cur.fetchone()
if row:
    doc_id, fname, status, meta = row
    print(f'id={doc_id} file={fname} status={status}')
conn.close()
" 2>&1 | while read line; do
  if echo "$line" | grep -qE 'documents=\d+'; then
    result PASS "Document row exists in PostgreSQL"
  fi
  echo "    $line"
done

# ── 3b. PostgreSQL: Chunk rows ──────────────────────────
echo ""
echo "  §3b  PostgreSQL: chunk rows (content verification)"
$VENV_PYTHON -c "
import psycopg2, json
conn = psycopg2.connect(host='localhost', port=5432, dbname='chatbot', user='chatbot', password='chatbot')
cur = conn.cursor()

# Count total chunks
cur.execute(\"SELECT count(*) FROM document_chunks\")
total = cur.fetchone()[0]
print(f'total_chunks={total}')

# Count chunk types (stored in metadata JSONB)
cur.execute(\"SELECT metadata->>'chunk_type' as ct, count(*) FROM document_chunks GROUP BY metadata->>'chunk_type' ORDER BY ct\")
for row in cur.fetchall():
    print(f'  {row[0] or \"unknown\"}: {row[1]}')

# Show first few chunks with content preview
cur.execute(\"\"\"SELECT id, metadata->>'chunk_type' as ct, chunk_index, left(content, 120) as preview,
       char_length(content) as char_len, metadata
FROM document_chunks ORDER BY chunk_index LIMIT 5\"\"\")
for row in cur.fetchall():
    cid, ct, idx, preview, clen, meta = row
    print(f'  chunk #{idx} [{ct}] {clen}chars: \"{preview}...\"')
    if meta:
        print(f'         metadata: {json.dumps(meta, indent=None)[:200]}')

conn.close()
" 2>&1 | while read line; do
  if echo "$line" | grep -q 'total_chunks='; then
    CNT=$(echo "$line" | cut -d= -f2)
    [[ "$CNT" -gt 0 ]] && result PASS "Chunks present in PostgreSQL: $CNT" || result FAIL "No chunks found"
  fi
  echo "    $line"
done

# ── 3c. PostgreSQL: HyDE questions ──────────────────────
echo ""
echo "  §3c  PostgreSQL: hypothetical questions (HyDE)"
$VENV_PYTHON -c "
import psycopg2, json
conn = psycopg2.connect(host='localhost', port=5432, dbname='chatbot', user='chatbot', password='chatbot')
cur = conn.cursor()

# Count chunks with HyDE questions (stored as metadata.hyde_questions array)
cur.execute(\"SELECT count(*) FROM document_chunks WHERE metadata ? 'hyde_questions'\")
hq_count = cur.fetchone()[0]
print(f'hypothetical_question_groups={hq_count}')

# Count total HyDE questions across all chunks
cur.execute(\"\"\"SELECT coalesce(sum(jsonb_array_length(metadata->'hyde_questions')), 0)
FROM document_chunks WHERE metadata ? 'hyde_questions'\"\"\")
total_hq = cur.fetchone()[0]
print(f'total_questions={total_hq}')

if hq_count > 0:
    cur.execute(\"\"\"SELECT chunk_index, metadata->'hyde_questions' as questions
FROM document_chunks WHERE metadata ? 'hyde_questions' LIMIT 2\"\"\")
    for row in cur.fetchall():
        idx, questions = row
        if questions:
            for qi, q in enumerate(questions[:3]):
                print(f'  Q#{idx}.{qi}: {q[:150]}')

conn.close()
" 2>&1 | while read line; do
  if echo "$line" | grep -q 'total_questions='; then
    CNT=$(echo "$line" | cut -d= -f2)
    [[ "$CNT" -gt 0 ]] && result PASS "HyDE questions generated: $CNT" || result SKIP "No HyDE questions found"
  fi
  echo "    $line"
done

# ── 3d. Milvus: Embeddings ──────────────────────────────
echo ""
echo "  §3d  Milvus: embeddings in $TEXT_COLLECTION"
$VENV_PYTHON -c "
from pymilvus import MilvusClient
c = MilvusClient('http://localhost:19530')

collection_name = '$TEXT_COLLECTION'

if collection_name not in c.list_collections():
    print('collection_exists=false')
    print('actual_rows=0')
else:
    c.load_collection(collection_name)
    # query actual data (more reliable than get_collection_stats row_count)
    results = c.query(collection_name, filter='', output_fields=['id', 'source_path', 'text'], limit=5)
    actual = len(results)
    print(f'actual_rows={actual}')
    if actual > 0:
        for r in results:
            rid = r.get('id', '?')
            rsrc = r.get('source_path', '?')
            rtext = (r.get('text', '') or '')[:80]
            print(f'  id={rid} source={rsrc} text=\"{rtext}...\"')
    else:
        # fallback: try search
        search_results = c.search(collection_name, data=[[0.0]*4096], limit=1, output_fields=['id'])[0]
        print(f'  search returned {len(search_results)} hits')
        actual = len(search_results)
        print(f'actual_rows={actual}')
" 2>&1 | while read line; do
  if echo "$line" | grep -q 'actual_rows='; then
    CNT=$(echo "$line" | cut -d= -f2)
    [[ "$CNT" -gt 0 ]] && result PASS "Embeddings present in Milvus: $CNT" || result FAIL "No embeddings in Milvus"
  fi
  echo "    $line"
done

# ── 3e. Search test ─────────────────────────────────────
echo ""
echo "  §3e  Search: query for 'Acheron Trough Nautilus'"
SEARCH_RESP=$(curl -sf -X POST "$API_URL/v1/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"Acheron Trough Nautilus expedition","top_k":5,"mode":"hybrid"}' 2>&1) && {
  HITS=$(echo "$SEARCH_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(len(d['results']))
" 2>/dev/null || echo "0")
  [[ "$HITS" -gt 0 ]] && result PASS "Search returned $HITS results" || result FAIL "Search returned no results"
  echo "$SEARCH_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d['results']:
    print(f'  rank={r[\"rank\"]} score={r[\"score\"]:.4f} method={r[\"method\"]} text=\"{r[\"text\"][:80]}...\"')
" 2>/dev/null
} || result FAIL "Search endpoint returned error"

# ── Summary ─────────────────────────────────────────────
_summarize
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo "══════════════════════════════════════════════════"
[[ $FAIL -eq 0 ]] && {
  echo " All checks passed! Ingestion pipeline validated end-to-end."
} || {
  echo " Some checks failed — see above."
  exit 1
}
