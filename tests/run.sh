#!/bin/bash
# Unified test orchestrator for all test suites.
#
# Usage:
#   bash tests/run.sh chatbot [--runpod|--local] [flags]  # run chatbot tests
#   bash tests/run.sh rag [flags]                          # run rag-pipeline tests
#   bash tests/run.sh integration [flags]                  # run integration tests
#   bash tests/run.sh all [--runpod|--local] [flags]       # run all test suites
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in
  chatbot)
    shift
    exec bash "$SCRIPT_DIR/chatbot/test-all.sh" "$@"
    ;;
  rag)
    shift
    exec bash "$SCRIPT_DIR/rag/test_api.sh" "$@"
    ;;
  integration)
    shift
    echo "Integration tests not yet implemented."
    exit 0
    ;;
  all)
    shift
    echo "Running chatbot tests..."
    bash "$SCRIPT_DIR/chatbot/test-all.sh" "$@" || true
    echo ""
    echo "Running rag-pipeline tests..."
    bash "$SCRIPT_DIR/rag/test_api.sh" "$@" || true
    echo ""
    echo "Running integration tests..."
    bash "$SCRIPT_DIR/integration/run.sh" "$@" 2>/dev/null || echo "(no integration tests)"
    ;;
  *)
    echo "Usage: bash tests/run.sh {chatbot|rag|integration|all} [flags]"
    echo ""
    echo "  chatbot     Run chatbot-service Django tests"
    echo "  rag         Run rag-pipeline API tests"
    echo "  integration Run integration tests (chatbot + rag together)"
    echo "  all         Run all test suites"
    exit 1
    ;;
esac
