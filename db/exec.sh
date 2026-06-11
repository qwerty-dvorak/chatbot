#!/bin/bash
# Execute a SQL query against the running PostgreSQL container.
#
# Usage: bash exec.sh "SELECT * FROM users;"
#        bash exec.sh < query.sql
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export $(grep -v '^#' "$SCRIPT_DIR/../models/.env.local" 2>/dev/null | xargs || true)

DB_NAME="${POSTGRES_DB:-chatbot}"
DB_USER="${POSTGRES_USER:-chatbot}"

if [ $# -ge 1 ]; then
  docker exec -i chatbot-postgres su - postgres -c "psql -d $DB_NAME" <<< "$1"
else
  docker exec -i chatbot-postgres su - postgres -c "psql -d $DB_NAME"
fi
