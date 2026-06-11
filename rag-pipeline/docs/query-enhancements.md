# Query Enhancement Strategies

Query enhancements improve retrieval quality by transforming the raw user query
into one or more richer queries before embedding and searching. All enhancements
make LLM calls, so they add latency — use the appropriate tier to control which
ones run.

## Available Enhancements

### HyDE (Hypothetical Document Embeddings) — Query-time

**Key insight:** The raw query "what is self-attention?" lives in a different
part of embedding space than the actual documents that answer it. A hypothetical
answer ("Self-attention is a mechanism where each token…") lives much closer to
the real documents.

**How it works:**
1. The LLM generates a short (2-4 sentence) hypothetical passage that would
   answer the query.
2. That passage is embedded **at query time** instead of (or in addition to)
   the raw query.
3. Retrieval finds chunks whose vectors are near the hypothetical passage.

**When to use:** Almost always. HyDE is the default enhancement for slow and
global tiers. It consistently improves recall for factual and descriptive
queries.

**Trade-off:** One extra LLM call per search. For instant-tier speed, skip it.

**Important:** HyDE is a **query-time** technique only. Do not confuse it with
**Hypothetical Questions** (index-time), which pre-generates question vectors
during ingestion.

---

### Sub-Queries — Query-time

**Key insight:** Complex questions ("compare transformer and RNN performance on
long sequences") have multiple aspects. A single embedding may not surface
documents that address each aspect.

**How it works:**
1. The LLM decomposes the query into 2-4 simpler, focused sub-questions.
2. Each sub-question is embedded and searched independently.
3. All result lists are merged via RRF fusion.
4. The original query is always appended so broad matches are not missed.

**When to use:** Multi-aspect, comparative, or open-ended questions. Global tier
only by default.

**Trade-off:** O(N) embedding + retrieval calls (N = number of sub-queries + 1).

---

### Stepback — Query-time

**Key insight:** Specific queries ("what is the learning rate in the paper on
page 7?") may miss documents that contain the background knowledge needed to
understand the answer. A broader query ("what optimisation techniques are used
in transformer training?") surfaces that context.

**How it works:**
1. The LLM reformulates the query into a broader, more general question.
2. The broader question is embedded and searched alongside the original.
3. Results are fused via RRF.

**When to use:** Narrow, specific queries where background context is needed.
Global tier only by default.

**Trade-off:** One extra LLM call. Less useful for already-broad queries.

---

### Hypothetical Questions — Index-time

**Key insight:** A chunk about "gradient descent converges when the learning
rate is below 1/L" won't match a query phrased as "how do I choose a safe
learning rate?". But if we index the question "what is a safe learning rate?"
as a separate vector in Milvus pointing back to the original chunk, both
phrasings will find it via question-to-question semantic matching.

**How it works (index-time flow):**
1. At ingest time, the LLM generates N questions that each chunk would answer
   (N = `hypothetical_questions_per_chunk`, 0 for instant, 2 for slow, 3 for global).
2. Each question is placed into a `Chunk` object with:
   - `chunk_type = HYPOTHETICAL_QUESTION`
   - `parent_id = <source chunk UUID>`
   - `text = <the question text>`
3. Question chunks are **embedded** using the same text embedding model as
   document chunks.
4. They are **indexed into Milvus** (same `rag_text_chunks` collection) as
   separate vectors with `chunk_type='hypothetical_question'`.

**How it works (query-time flow):**
1. User query is embedded normally (possibly with query-time enhancements like
   HyDE, sub-queries, or stepback).
2. Vector search returns both document chunks and hypothetical question chunks.
3. For every result of type HYPOTHETICAL_QUESTION:
   - The pipeline queries Milvus by `parent_id` to find the source document chunk.
   - The question result is **replaced** with the source chunk.
   - `retrieval_method` is set to `"query_to_query"`.
4. If the same source chunk was also found by direct document-vector search, the
   duplication is eliminated during dedup (highest score wins).

**Why this is different from HyDE:**

| Aspect | HyDE | Hypothetical Questions |
|--------|------|------------------------|
| When | Query-time | Index-time |
| What is generated | Hypothetical *document* (answers query) | Hypothetical *questions* (that chunks answer) |
| What gets embedded | The generated document text | The generated question text |
| Where stored | Not stored — generated fresh each query | Stored in Milvus as persistent vectors |
| Search target | Document chunk vectors | Question vectors + query-to-query resolution |
| Configuration | `QUERY_ENHANCEMENTS=hyde` | `HYPOTHETICAL_QUESTIONS_PER_CHUNK=N` |

Both can be active simultaneously. HyDE transforms the query string, then the
transformed query naturally matches question vectors during the index-time
hypothetical question search.

**Trade-off:** Index-time LLM calls: O(N × Q) where N = chunks and Q = questions
per chunk. No query-time overhead for the question generation itself (the
vectors are already computed).

---

## LLM Endpoints

Enhancement LLM calls use the configured chat endpoint:

| Tier | LLM Used | Config |
|------|---------|--------|
| instant | — (no enhancements) | — |
| slow | Default chat model | `CHAT_BASE_URL` / `CHAT_MODEL` |
| global | Configured local chat model | `CHAT_BASE_URL` / `CHAT_MODEL` |

Configure the endpoint with `CHAT_BASE_URL`, `CHAT_API_KEY`, and `CHAT_MODEL` in
the RAG pipeline environment. Instant-tier search makes no enhancement call.

## Configuring Enhancements

### Per tier (static defaults in `tiers.py`)

```python
# pipeline/tiers.py
GLOBAL_OPTIONS = IngestOptions(
    ...
    query_enhancements=("hyde", "sub_queries", "stepback"),
    use_reranker=True,
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

This env var is used when no tier is specified and no explicit enhancements are
passed.

## Enhancement Interaction

When multiple enhancements are active, all generated query strings are merged via
Reciprocal Rank Fusion (RRF):

```
original query
  ├── hyde → hypothetical_passage
  ├── sub_queries → [sub_q1, sub_q2, original]
  └── stepback → stepback_question + original
```

Each of the above is embedded and searched independently, then all result lists
are fused. Duplicates are removed (highest score kept). The reranker then
re-scores the top-K merged results using the **original query** (not the
enhanced variants), preventing semantic drift.

On top of this fusion, every result list is also subjected to **query-to-query
resolution**: any HYPOTHETICAL_QUESTION result is resolved to its parent
document chunk (see the Hypothetical Questions section above).

## Performance Benchmarks (approximate)

| Enhancement | Extra LLM calls | Latency overhead |
|-------------|----------------|-----------------|
| none | 0 | 0 ms |
| hyde | 1 | ~200-500 ms |
| sub_queries | 1 (returns 2-4 Qs) | ~300-600 ms + 2-4× retrieval |
| stepback | 1 | ~200-400 ms + 1× retrieval |
| all three | 3 | ~700 ms - 1.5 s + extra retrievals |
| hypothetical questions (index-time) | N×Q at ingest | 0 ms at query time (vectors precomputed) |

Latency is highly dependent on the LLM endpoint; these are rough estimates for a
local vLLM serving a 7B model.
