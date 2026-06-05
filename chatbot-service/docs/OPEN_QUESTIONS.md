# Open Questions

These were the original open questions and their current answers. Only implementation-specific verification items remain.

1. Which exact model will be used for chat?

   Answered: `Gemma 4 26B A4B IT`, MoE architecture with 256K context window, multimodal (text + images).

2. Which models are used for RAG?

   Answered:
   - Text embedding: `nvidia/llama-embed-nemotron-8b` (dim: 4096)
   - Multimodal embedding: `nvidia/nemotron-colembed-vl-8b-v2` (dim: 4096, late-interaction)
   - Reranker: `Qwen3-VL-Reranker-8B` (cross-encoder, score output)

3. Which vector store is used?

   Answered: Milvus (replaces pgvector). Two collections: `document_chunks` (dim 4096) and `user_memories` (dim 4096).

4. Does the local model runtime support multimodal image input?

   Answered: yes, multimodal tasks are required. Implementation should still keep OCR fallback optional for PDFs/images where model vision is insufficient.

5. Should uploaded knowledge be private by default?

   Answered: yes, uploaded knowledge is private to the user by default.

6. Is live token streaming required?

   Answered: yes, streaming is required. Use browser-native streaming without npm.

7. Which file types are mandatory for the first version?

   Answered: text, Markdown, PDF, images, and CSV are required.

8. Should chat shares be public links or only authenticated-user links?

   Answered: public links are required.

9. Should memory be auto-saved by default?

   Answered: auto-save is required and customizable.

10. Are background workers allowed?

    Answered: background workers are allowed.

11. Should the system support multiple users in the same local deployment?

    Answered: yes, multiple users should work through login/logout.

## Remaining Verification Items

1. Confirm the exact embedding vector dimensions for all models before creating Milvus collections.
2. Confirm whether Gemma 4 26B A4B IT will run behind llama.cpp, vLLM, Ollama, or another OpenAI-compatible server.
3. Confirm the Milvus version and connection details for the target deployment.
4. Choose the background worker implementation: management-command loop, Celery, RQ, or another Python-only option.
5. Decide whether public share links include only stored chat messages or also allow live reruns against the owner's knowledge base. Recommended: stored chat messages only.
