#!/bin/bash
# Check PostgreSQL health.
#
# Usage: bash health.sh
set -euo pipefail

STATUS=$(docker inspect -f '{{.State.Health.Status}}' chatbot-postgres 2>/dev/null || echo "not-found")
echo "PostgreSQL health: $STATUS"
if [[ "$STATUS" == "healthy" ]]; then
  docker exec chatbot-postgres su - postgres -c "psql -d chatbot -c 'SELECT 1 AS ping;'"
fi
