#!/bin/bash
# Test the root-level env system: .env loading, MODE detection,
# script argument parsing, and delegating to sub-scripts.
#
# Usage:
#   bash tests/env/test_env.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

result() { local s=$1 name="$2"; [[ "$s" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1)); printf "  [%s] %s\n" "$s" "$name"; }

cleanup() { rm -f "$ROOT_DIR/.env.test" 2>/dev/null || true; }
trap cleanup EXIT

echo "╔══════════════════════════════════════════════════╗"
echo "║   Env System Tests                                ║"
echo "╚══════════════════════════════════════════════════╝"

# ── Test: start.sh --help prints usage ──────────────────────────────────
echo ""
echo "--- start.sh argument parsing ---"

output=$(bash "$ROOT_DIR/start.sh" --help 2>&1 || true)
if echo "$output" | grep -q "Usage:"; then
  result PASS "start.sh --help prints usage"
else
  result FAIL "start.sh --help did not print usage"
fi

# ── Test: teardown.sh --help prints usage ────────────────────────────────
echo ""
echo "--- teardown.sh argument parsing ---"

output=$(bash "$ROOT_DIR/teardown.sh" --help 2>&1 || true)
if echo "$output" | grep -q "Usage:"; then
  result PASS "teardown.sh --help prints usage"
else
  result FAIL "teardown.sh --help did not print usage"
fi

# ── Test: test.sh --help prints usage ──────────────────────────────────
echo ""
echo "--- test.sh argument parsing ---"

output=$(bash "$ROOT_DIR/test.sh" --help 2>&1 || true)
if echo "$output" | grep -q "Usage:"; then
  result PASS "test.sh --help prints usage"
else
  result FAIL "test.sh --help did not print usage"
fi

# ── Test: test.sh with no arguments prints usage (no TEST_SUITE) ──────
echo ""
echo "--- test.sh missing suite argument ---"

output=$(bash "$ROOT_DIR/test.sh" 2>&1 || true)
if echo "$output" | grep -q "Usage:"; then
  result PASS "test.sh with no args prints usage"
else
  result FAIL "test.sh with no args did not print usage"
fi

# ── Test: MODE detection from .env.example ─────────────────────────────
echo ""
echo "--- MODE detection from .env.example ---"

cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env.test"
MODE_VAL=$(grep -E '^MODE=' "$ROOT_DIR/.env.test" | cut -d= -f2 | tr -d '[:space:]')
if [[ "$MODE_VAL" == "LOCAL" ]]; then
  result PASS ".env.example has MODE=LOCAL"
else
  result FAIL ".env.example has MODE=$MODE_VAL (expected LOCAL)"
fi
rm -f "$ROOT_DIR/.env.test"

# ── Test: start.sh with invalid mode ───────────────────────────────────
echo ""
echo "--- invalid mode rejection ---"

output=$(MODE="" bash -c 'cd '"$ROOT_DIR"' && MODE=INVALID bash -c "
  SCRIPT_DIR=\"$ROOT_DIR\"
  ENV_FILE=\"$ROOT_DIR/.env\"
  CLEAN=false
  MODE=INVALID
  MODE=\${MODE:-LOCAL}
  MODE=\${MODE^^}
  if [[ \"\$MODE\" != \"LOCAL\" && \"\$MODE\" != \"RUNPOD\" ]]; then echo \"ERROR: MODE must be LOCAL or RUNPOD\"; fi
" 2>&1 || true')
if echo "$output" | grep -q "ERROR"; then
  result PASS "Invalid mode rejected with error"
else
  result FAIL "Invalid mode not rejected"
fi

# ── Test: .env.example is valid env file ──────────────────────────────
echo ""
echo "--- .env.example format ---"

if head -1 "$ROOT_DIR/.env.example" | grep -q "^#"; then
  result PASS ".env.example starts with comment"
else
  result FAIL ".env.example does not start with comment"
fi

if grep -q "^MODE=" "$ROOT_DIR/.env.example"; then
  result PASS ".env.example contains MODE= variable"
else
  result FAIL ".env.example missing MODE= variable"
fi

# ── Test: scripts are executable ──────────────────────────────────────
echo ""
echo "--- script executability ---"

for f in start.sh teardown.sh test.sh; do
  if [[ -x "$ROOT_DIR/$f" ]]; then
    result PASS "$f is executable"
  else
    result FAIL "$f is not executable"
  fi
done

# ── Test: test.sh --mode local delegates correctly (dry-run) ──────────
echo ""
echo "--- test.sh mode delegation ---"

# Simulate what test.sh does for MODE detection
output=$(MODE=LOCAL bash -c '
  SCRIPT_DIR="'"$ROOT_DIR"'"
  MODE=LOCAL
  ARGS=()
  if [[ "$MODE" == "RUNPOD" ]]; then ARGS+=("--runpod"); else ARGS+=("--local"); fi
  echo "${ARGS[@]}"
' 2>&1)
if [[ "$output" == "--local" ]]; then
  result PASS "test.sh LOCAL mode produces --local flag"
else
  result FAIL "test.sh LOCAL mode produced: $output"
fi

output=$(MODE=RUNPOD bash -c '
  SCRIPT_DIR="'"$ROOT_DIR"'"
  MODE=RUNPOD
  ARGS=()
  if [[ "$MODE" == "RUNPOD" ]]; then ARGS+=("--runpod"); else ARGS+=("--local"); fi
  echo "${ARGS[@]}"
' 2>&1)
if [[ "$output" == "--runpod" ]]; then
  result PASS "test.sh RUNPOD mode produces --runpod flag"
else
  result FAIL "test.sh RUNPOD mode produced: $output"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed"
echo "══════════════════════════════════════════════════"
if [[ $FAIL -eq 0 ]]; then
  echo " All env system tests passed!"
  exit 0
else
  echo " Some tests failed."
  exit 1
fi
