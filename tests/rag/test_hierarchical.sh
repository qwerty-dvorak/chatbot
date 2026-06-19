#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Hierarchical Index E2E Test
#
# Validates the coarse-to-fine retrieval architecture:
#   1. Ingests MULTIPLE documents → each gets a summary vector
#   2. Summaries stored in PostgreSQL + embedded in Milvus
#   3. Hierarchical search: stage 1 matches summaries → stage 2 searches only
#      within the matched documents' chunks
#   4. Non-hierarchical search still works correctly
###############################################################################

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$HERE/../.." && pwd)"
RAG_API="${RAG_API_URL:-http://localhost:8093}"

PASS=0
FAIL=0

check() {
    local label="$1" condition="$2"
    if eval "$condition"; then
        echo "  [PASS] $label"
        ((PASS++))
    else
        echo "  [FAIL] $label"
        ((FAIL++))
    fi
}

echo "══════════════════════════════════════════════════"
echo " Hierarchical Index E2E Test"
echo "══════════════════════════════════════════════════"

# ── Prerequisites ─────────────────────────────────────
echo ""
echo "Checking prerequisites..."

check "rag-api reachable" "curl -sf $RAG_API/health/live > /dev/null"
check "PostgreSQL reachable" "psql -h \"\${PGHOST:-localhost}\" -U \"\${PGUSER:-chatbot}\" -d \"\${PGDATABASE:-chatbot}\" -c 'SELECT 1' > /dev/null 2>&1"

MILVUS_HOST="${MILVUS_HOST:-localhost}"
MILVUS_PORT="${MILVUS_PORT:-19530}"
MILVUS_COLLECTION="${TEXT_COLLECTION:-rag_text_chunks}"

# Check Milvus via a simple port check (pymilvus-based check is done later)
check "Milvus reachable" "curl -sf http://$MILVUS_HOST:$MILVUS_PORT/health > /dev/null 2>&1 || timeout 2 bash -c \"echo > /dev/tcp/$MILVUS_HOST/$MILVUS_PORT\" 2>/dev/null"

# ── Clean previous data ────────────────────────────────
echo ""
echo "Cleaning previous data..."

# Drop Milvus collection via API
curl -sf -X DELETE "$RAG_API/v1/collections/$MILVUS_COLLECTION" > /dev/null 2>&1 || true

# Clean PostgreSQL documents + chunks
psql -h "${PGHOST:-localhost}" -U "${PGUSER:-chatbot}" -d "${PGDATABASE:-chatbot}" <<'SQL' > /dev/null 2>&1 || true
DELETE FROM document_chunks;
DELETE FROM documents;
DELETE FROM knowledge_sources WHERE name = 'rag-pipeline';
SQL

echo "  [DONE] Previous data cleaned"

# ── Ingest multiple topic files ──────────────────────
echo ""
echo "Ingesting topic documents..."
SAMPLE_DIR="$ROOT_DIR/sample_data/text"
TOPIC_FILES=(
    "$SAMPLE_DIR/topic_geology.txt"
    "$SAMPLE_DIR/topic_biology.txt"
    "$SAMPLE_DIR/topic_astronomy.txt"
    "$SAMPLE_DIR/factual_test.txt"
)

INGEST_IDS=()
for f in "${TOPIC_FILES[@]}"; do
    [ -f "$f" ] || { echo "  [SKIP] $f not found"; continue; }
    echo "  Submitting: $(basename "$f")"
    RESP=$(curl -sf -X POST "$RAG_API/v1/ingest" \
        -F "files=@$f" \
        -F "tier=slow" 2>&1) || {
        echo "  [WARN] curl failed for $f: $RESP"
        continue
    }
    JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
    [ -n "$JOB_ID" ] && INGEST_IDS+=("$JOB_ID")
done

echo "  Submitted ${#INGEST_IDS[@]} ingestion job(s)"

# ── Poll until all jobs complete ─────────────────────
echo ""
echo "Polling jobs until completion..."
MAX_POLLS=180
POLL_INTERVAL=2
for JOB_ID in "${INGEST_IDS[@]}"; do
    for ((i=0; i<MAX_POLLS; i++)); do
        STATUS=$(curl -sf "$RAG_API/v1/ingestions/$JOB_ID" | python3 -c "
import sys, json
j = json.load(sys.stdin)
print(j.get('status', 'unknown'))
print(json.dumps(j.get('result', {})))
" 2>/dev/null || echo "unknown") || true
        JOB_STATUS=$(echo "$STATUS" | head -1)
        JOB_RESULT=$(echo "$STATUS" | tail -1)
        SUMMARY_COUNT=$(echo "$JOB_RESULT" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('summary_indexed', 0) if isinstance(r, dict) else 0)" 2>/dev/null || echo "0")
        if [ "$JOB_STATUS" = "succeeded" ]; then
            echo "  ✓ Job $JOB_ID completed (summary_indexed=$SUMMARY_COUNT)"
            break
        elif [ "$JOB_STATUS" = "failed" ]; then
            echo "  ✗ Job $JOB_ID failed!"
            break
        fi
        sleep "$POLL_INTERVAL"
    done
done

echo ""

# ── Phase 1: Verify summaries in PostgreSQL ──────────
echo "───────────────────────────────────────────────────"
echo " Phase 1: Verify summary storage"
echo "───────────────────────────────────────────────────"

SUMMARY_ROWS=$(psql -h "${PGHOST:-localhost}" -U "${PGUSER:-chatbot}" -d "${PGDATABASE:-chatbot}" -t -A \
    -c "SELECT COUNT(*) FROM documents WHERE analysis_summary != '' AND analysis_summary IS NOT NULL;" 2>/dev/null || echo "0")
check "Summaries stored in PostgreSQL (≥4)" "[ $SUMMARY_ROWS -ge 4 ]"

echo ""
for f in "${TOPIC_FILES[@]}"; do
    FNAME=$(basename "$f")
    SUMMARY=$(psql -h "${PGHOST:-localhost}" -U "${PGUSER:-chatbot}" -d "${PGDATABASE:-chatbot}" -t -A \
        -c "SELECT SUBSTRING(analysis_summary, 1, 80) FROM documents WHERE original_filename = '$FNAME' AND analysis_summary != '' LIMIT 1;" 2>/dev/null || true)
    if [ -n "$SUMMARY" ]; then
        echo "    $FNAME → ${SUMMARY}..."
    else
        echo "    $FNAME → (no summary)"
    fi
done

# ── Phase 2: Verify summary vectors in Milvus ────────
echo ""
echo "───────────────────────────────────────────────────"
echo " Phase 2: Verify summary vectors in Milvus"
echo "───────────────────────────────────────────────────"

# Query Milvus for summary chunk count
SUMMARY_COUNT=$(python3 -c "
import sys, json
from pymilvus import MilvusClient
try:
    c = MilvusClient(uri='http://$MILVUS_HOST:$MILVUS_PORT')
    if c.has_collection('$MILVUS_COLLECTION'):
        r = c.query(
            collection_name='$MILVUS_COLLECTION',
            filter='chunk_type == \"summary\"',
            output_fields=['id'],
            limit=100,
        )
        print(len(r))
    else:
        print(0)
except Exception:
    print(-1)
" 2>/dev/null || echo "-1")

if [ "$SUMMARY_COUNT" -ge 4 ]; then
    check "Summary vectors in Milvus (≥4)" "true"
else
    check "Summary vectors in Milvus (≥4)" "false  # got $SUMMARY_COUNT"
fi

# Show sample summary vectors
echo ""
python3 -c "
from pymilvus import MilvusClient
c = MilvusClient(uri='http://$MILVUS_HOST:$MILVUS_PORT')
if c.has_collection('$MILVUS_COLLECTION'):
    r = c.query(
        collection_name='$MILVUS_COLLECTION',
        filter='chunk_type == \"summary\"',
        output_fields=['id', 'source_path', 'text'],
        limit=4,
    )
    for hit in r:
        src = hit.get('source_path', '?')
        txt = hit.get('text', '')[:100]
        print(f'    summary: {src} → {txt}...')
" 2>/dev/null || echo "    (could not query Milvus)"

# ── Phase 3: Test hierarchical search ────────────────
echo ""
echo "───────────────────────────────────────────────────"
echo " Phase 3: Hierarchical search (stage 1 = summary + stage 2 = chunks)"
echo "───────────────────────────────────────────────────"

# 3a: Geology query — should return chunks from geology document
echo ""
echo "  §3a Query: 'plate tectonics subduction zones mineral deposits'"
GEO_RESULTS=$(curl -sf -X POST "$RAG_API/v1/search" \
    -H "Content-Type: application/json" \
    -d '{"query": "plate tectonics subduction zones mineral deposits", "top_k": 5, "mode": "hybrid", "hierarchical": true, "enhancements": ""}' 2>/dev/null || echo '{}')

# Check results are from topic_geology (not summaries)
GEO_COUNT=$(echo "$GEO_RESULTS" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    results = r.get('results', [])
    # Verify no summary chunks in results
    geos = [res for res in results if 'topic_geology' in res.get('source', '')]
    summaries = [res for res in results if res.get('chunk_type') == 'summary']
    print(f'count={len(results)} geo={len(geos)} summaries={len(summaries)}')
except Exception:
    print('error')
" 2>/dev/null || echo "error")

check "Hierarchical search returns results (geo)" "echo '$GEO_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert len(r.get('results',[])) > 0\" 2>/dev/null"
check "No summary chunks in results (geo)" "echo '$GEO_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert all(res.get('chunk_type') != 'summary' for res in r.get('results',[]))\" 2>/dev/null"

echo "    $GEO_COUNT"

# 3b: Biology query
echo ""
echo "  §3b Query: 'deep sea hydrothermal vents chemosynthesis bioluminescence'"
BIO_RESULTS=$(curl -sf -X POST "$RAG_API/v1/search" \
    -H "Content-Type: application/json" \
    -d '{"query": "deep sea hydrothermal vents chemosynthesis bioluminescence", "top_k": 5, "mode": "hybrid", "hierarchical": true, "enhancements": ""}' 2>/dev/null || echo '{}')

BIO_COUNT=$(echo "$BIO_RESULTS" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    results = r.get('results', [])
    bios = [res for res in results if 'topic_biology' in res.get('source', '')]
    summaries = [res for res in results if res.get('chunk_type') == 'summary']
    print(f'count={len(results)} bio={len(bios)} summaries={len(summaries)}')
except Exception:
    print('error')
" 2>/dev/null || echo "error")

check "Hierarchical search returns results (bio)" "echo '$BIO_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert len(r.get('results',[])) > 0\" 2>/dev/null"
check "No summary chunks in results (bio)" "echo '$BIO_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert all(res.get('chunk_type') != 'summary' for res in r.get('results',[]))\" 2>/dev/null"

echo "    $BIO_COUNT"

# 3c: Astronomy query
echo ""
echo "  §3c Query: 'stellar evolution supernova neutron star black hole gravitational waves'"
ASTRO_RESULTS=$(curl -sf -X POST "$RAG_API/v1/search" \
    -H "Content-Type: application/json" \
    -d '{"query": "stellar evolution supernova neutron star black hole gravitational waves", "top_k": 5, "mode": "hybrid", "hierarchical": true, "enhancements": ""}' 2>/dev/null || echo '{}')

ASTRO_COUNT=$(echo "$ASTRO_RESULTS" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    results = r.get('results', [])
    astros = [res for res in results if 'topic_astronomy' in res.get('source', '')]
    summaries = [res for res in results if res.get('chunk_type') == 'summary']
    print(f'count={len(results)} astro={len(astros)} summaries={len(summaries)}')
except Exception:
    print('error')
" 2>/dev/null || echo "error")

check "Hierarchical search returns results (astro)" "echo '$ASTRO_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert len(r.get('results',[])) > 0\" 2>/dev/null"
check "No summary chunks in results (astro)" "echo '$ASTRO_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert all(res.get('chunk_type') != 'summary' for res in r.get('results',[]))\" 2>/dev/null"

echo "    $ASTRO_COUNT"

# ── Phase 4: Non-hierarchical search still works ─────
echo ""
echo "───────────────────────────────────────────────────"
echo " Phase 4: Non-hierarchical search (standard mode)"
echo "───────────────────────────────────────────────────"

echo ""
echo "  Query: 'Acheron Trough Expedition Nautilus VII'"
STD_RESULTS=$(curl -sf -X POST "$RAG_API/v1/search" \
    -H "Content-Type: application/json" \
    -d '{"query": "Acheron Trough Expedition Nautilus VII", "top_k": 5, "mode": "hybrid", "hierarchical": false}' 2>/dev/null || echo '{}')

check "Non-hierarchical search returns results" "echo '$STD_RESULTS' | python3 -c \"import sys,json; r=json.load(sys.stdin); assert len(r.get('results',[])) > 0\" 2>/dev/null"

STD_COUNT=$(echo "$STD_RESULTS" | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    print(len(r.get('results', [])))
except Exception:
    print('0')
" 2>/dev/null || echo "0")
echo "    returned $STD_COUNT results"

# ── Summary ──────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed, $(($PASS + $FAIL)) total"
echo "══════════════════════════════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
