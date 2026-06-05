# Query Enhancement Strategies

Query enhancements improve retrieval quality by transforming the raw user query into
one or more richer queries before embedding and searching.  All enhancements make LLM
calls, so they add latency — use the appropriate tier to control which ones run.

## Available Enhancements

### HyDE (Hypothetical Document Embeddings)

**Key insight:** The raw query "what is self-attention?" lives in a different part of
embedding space than the actual documents that answer it.  A hypothetical answer
("Self-attention is a mechanism where each token…") lives much closer to the real
documents.

**How it works:**
1. The LLM generates a short (2-4 sentence) hypothetical passage that would answer the query.
2. That passage is embedded instead of (or in addition to) the raw query.
3. Retrieval finds chunks near the hypothetical passage.

**When to use:** Almost always.  HyDE is the default enhancement for slow and global tiers.
It consistently improves recall for factual and descriptive queries.

**Trade-off:** One extra LLM call per search.  For instant-tier speed, skip it.

---

### Sub-Queries

**Key insight:** Complex questions ("compare transformer and RNN performance on long sequences")
have multiple aspects.  A single embedding may not surface documents that address each aspect.

**How it works:**
1. The LLM decomposes the query into 2-4 simpler, focused sub-questions.
2. Each sub-question is embedded and searched independently.
3. All result lists are merged via RRF fusion.
4. The original query is always appended so broad matches are not missed.

**When to use:** Multi-aspect, comparative, or open-ended questions.  Global tier only by default.

**Trade-off:** O(N) embedding + retrieval calls (N = number of sub-queries + 1).

---

### Stepback

**Key insight:** Specific queries ("what is the learning rate in the paper on page 7?") may miss
documents that contain the background knowledge needed to understand the answer.  A broader query
("what optimisation techniques are used in transformer training?") surfaces that context.

**How it works:**
1. The LLM reformulates the query into a broader, more general question.
2. The broader question is embedded and searched alongside the original.
3. Results are fused via RRF.

**When to use:** Narrow, specific queries where background context is needed.  Global tier only by default.

**Trade-off:** One extra LLM call.  Less useful for already-broad queries.

---

### Hypothetical Questions (Index Time)

**Key insight:** A chunk about "gradient descent converges when the learning rate is below 1/L"
won't match a query phrased as "how do I choose a safe learning rate?".  But if we index the
question "what is a safe learning rate?" as a separate chunk pointing back to the original,
both phrasings will find it.

**How it works:**
1. At ingest time, the LLM generates N questions that each chunk would answer.
2. Each question is stored as a separate chunk in Milvus, with `parent_id` pointing to the original.
3. At search time, queries that match the generated questions surface the original chunk.

**Configuration:** Controlled by `hypothetical_questions_per_chunk` in `IngestOptions`
(0 = disabled for instant, 2 for slow, 3 for global).

**Trade-off:** Index-time LLM calls: O(N × Q) where N = chunks and Q = questions per chunk.
Not applied at search time.

## LLM Endpoints

Enhancement LLM calls use one of two endpoints depending on tier:

| Tier | LLM Used | Config |
|------|---------|--------|
| instant | — (no enhancements) | — |
| slow | Default chat model | `CHAT_BASE_URL` / `CHAT_MODEL` |
| global | Chatbot-service LLM | `CHATBOT_LLM_BASE_URL` / `CHATBOT_LLM_MODEL` |

The chatbot-service LLM (global tier) is typically the same powerful vLLM endpoint used
by the Django chatbot.  It should be configured via `CHATBOT_LLM_BASE_URL` in the RAG
pipeline's `.env`.  When `CHATBOT_LLM_BASE_URL` is not set, global-tier enhancements
fall back to `CHAT_BASE_URL` gracefully.

## Configuring Enhancements

### Per tier (static defaults in `tiers.py`)

```python
# pipeline/tiers.py
GLOBAL_OPTIONS = IngestOptions(
    ...
    query_enhancements=("hyde", "sub_queries", "stepback"),
    use_chatbot_llm=True,
)
```

### Per request (API)

```bash
# Override with explicit enhancement list
curl -X POST http://localhost:8093/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "enhancements": "hyde,sub_queries"}'
```

### Per env (default for legacy `QUERY_ENHANCEMENTS`)

```
QUERY_ENHANCEMENTS=hyde
```

This env var is used when no tier is specified and no explicit enhancements are passed.

## Enhancement Interaction

When multiple enhancements are active, all generated query strings are merged via
Reciprocal Rank Fusion (RRF):

```
original query
  ├── hyde → hypothetical_passage
  ├── sub_queries → [sub_q1, sub_q2, original]
  └── stepback → stepback_question + original
```

Each of the above is embedded and searched independently, then all result lists are
fused.  Duplicates are removed (highest score kept).  The reranker then re-scores
the top-K merged results using the **original query** (not the enhanced variants),
preventing semantic drift.

## Performance Benchmarks (approximate)

| Enhancement | Extra LLM calls | Latency overhead |
|-------------|----------------|-----------------|
| none | 0 | 0 ms |
| hyde | 1 | ~200-500 ms |
| sub_queries | 1 (returns 2-4 Qs) | ~300-600 ms + 2-4× retrieval |
| stepback | 1 | ~200-400 ms + 1× retrieval |
| all three | 3 | ~700 ms - 1.5 s + extra retrievals |

Latency is highly dependent on the LLM endpoint; these are rough estimates for a
local vLLM serving a 7B model.
