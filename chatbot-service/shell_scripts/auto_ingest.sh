#!/bin/bash
# Wait for RAG API to be healthy, then ingest global seed data.
# Called during web container startup. Fails silently so the container still starts.
set +e
API="${RAG_API_BASE_URL:-http://localhost:8093}"
for i in $(seq 1 30); do
  if curl -sf "${API}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
uv run python manage.py ingest_global_knowledge --settings=config.settings.production
