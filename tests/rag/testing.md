# RAG Pipeline Tests

End-to-end tests for the document ingestion, indexing, and search pipeline.
Python test files live in `rag-pipeline/tests/`. Shell orchestrators are in `tests/rag/`.

## Quick Start (any mode)

```bash
# Start the full pipeline from root
bash start.sh

# Run the ingest e2e test
bash tests/rag/test_ingest_e2e.sh

# Run hybrid+reranker test
bash tests/rag/test_hybrid_rerank.sh

# Run hierarchical index test
bash tests/rag/test_hierarchical.sh
```

All Python commands run inside Docker containers (`docker exec` / `docker run`).
Test data comes from the root `sample_data/` directory.

## Files

| File | Purpose |
|------|---------|
| `test_api.sh` | Full end-to-end: health, ingest, search (hybrid/vector/bm25) |
| `test_runpod_endpoints.sh` | Validate vLLM endpoints against `.env.runpod` |
| `test_ingest_e2e.sh` | End-to-end ingestion test |
| `test_hierarchical.sh` | Hierarchical search test |
| `test_hybrid_rerank.sh` | Hybrid reranker test |
| `rag-pipeline/tests/test_milvus.py` | Milvus connectivity tests |
| `rag-pipeline/tests/test_image_ocr.py` | Image splitting and OCR-mode contracts |
| `rag-pipeline/tests/test_hybrid_rerank.py` | Hybrid search + reranker tests |
| `rag-pipeline/tests/test_hyde.py` | Hypothetical Document Embeddings tests |
| `rag-pipeline/tests/test_hierarchical.py` | Hierarchical chunking tests |
| `rag-pipeline/tests/test_endpoints.py` | Endpoint validation tests |
