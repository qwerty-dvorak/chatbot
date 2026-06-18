# RAG Pipeline Tests

End-to-end tests for the document ingestion, indexing, and search pipeline.
See [`TESTING.md`](../TESTING.md) for comprehensive testing documentation.

## Quick Start (Mock Servers — No GPUs)

```bash
# Start all mock services + Milvus:
bash rag-pipeline/start_mock_all.sh

# Run full integration test:
bash tests/rag/test_api.sh

# Stop everything:
bash rag-pipeline/start_mock_all.sh --clean
```

## RunPod Tests

```bash
export HF_TOKEN=hf_...
bash models/deploy-runpod.sh
bash tests/rag/test_api.sh
bash models/teardown-runpod.sh
```

## Files

| File | Purpose |
|------|---------|
| `test_api.sh` | Full end-to-end: health, ingest, search (hybrid/vector/bm25) |
| `test_runpod_endpoints.sh` | Validate vLLM endpoints against `.env.runpod` |
| `test_milvus.py` | Milvus connectivity tests |
| `test_text_pipeline.py` | Chunk → embed → index unit tests |
| `test_image_pipeline.py` | Image document pipeline tests |
| `test_jobs.py` | Job queue tests |
| `test_progress.py` | Ingestion progress tracking tests |
| `test_hybrid_rerank.py` | Hybrid search + reranker tests |
| `test_hyde.py` | Hypothetical Document Embeddings tests |
| `test_hierarchical.py` | Hierarchical chunking tests |
| `test_object_store.py` | Object store abstraction tests |
| `test_ingest_e2e.sh` | End-to-end ingestion test |
| `test_hierarchical.sh` | Hierarchical search test |
| `test_hybrid_rerank.sh` | Hybrid reranker test |
