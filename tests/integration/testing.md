# Integration Tests

Cross-service tests that exercise the full pipeline (chatbot + RAG + infrastructure).

## Status

Not yet implemented.

## Planned Coverage

- Chat with RAG context injection
- Document upload → search → chat flow
- Cross-service API calls (chatbot ↔ RAG pipeline)
- Memory persistence across sessions
- Tool execution end-to-end

## Expected Flow

```bash
# 1. Start infrastructure
bash db/start.sh
bash milvus/start.sh

# 2. Start RAG API
bash rag-pipeline/run.sh runpod

# 3. Start Chatbot
bash chatbot-service/run.sh runpod

# 4. Run integration tests
bash tests/integration/run.sh
```

## Files

| File | Purpose |
|------|---------|
| `run.sh` | Placeholder orchestrator (prints expected flow, exits 0) |
