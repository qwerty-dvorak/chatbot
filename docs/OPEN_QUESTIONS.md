# Open Questions

These were the original open questions and their current answers. Only implementation-specific verification items remain.

1. Which exact Qwen model will be used for chat?

   Answered: `Jackrong/Qwopus3.6-27B-v2-GGUF`, 6-bit, native support up to 32K / 128K context length depending on runtime configuration.

2. Which exact embedding model should be used?

   Answered: `nvidia/llama-embed-nemotron-8b`.

3. Does the local Qwen runtime support multimodal image input?

   Answered: yes, multimodal tasks are required. Implementation should still keep OCR fallback optional for PDFs/images where model vision is insufficient.

4. Should uploaded knowledge be private by default?

   Answered: yes, uploaded knowledge is private to the user by default.

5. Is live token streaming required?

   Answered: yes, streaming is required. Use browser-native streaming without npm.

6. Which file types are mandatory for the first version?

   Answered: text, Markdown, PDF, images, and CSV are required.

7. Should chat shares be public links or only authenticated-user links?

   Answered: public links are required.

8. Should memory be auto-saved by default?

   Answered: auto-save is required and customizable.

9. Are background workers allowed?

   Answered: background workers are allowed.

10. Should the system support multiple users in the same local deployment?

   Answered: yes, multiple users should work through login/logout.

## Remaining Verification Items

1. Confirm the exact embedding vector dimension returned by `nvidia/llama-embed-nemotron-8b` in the chosen local runtime before creating pgvector migrations.
2. Confirm whether `Jackrong/Qwopus3.6-27B-v2-GGUF` will run behind llama.cpp, vLLM, Ollama, LiteLLM proxy, or another OpenAI-compatible server.
3. Confirm whether the 32K or 128K context setting is the default deployment target.
4. Choose the background worker implementation: management-command loop, Celery, RQ, or another Python-only option.
5. Decide whether public share links include only stored chat messages or also allow live reruns against the owner's knowledge base. Recommended: stored chat messages only.
