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
1. The LLM generates N (default 2) short (2-4 sentence) hypothetical passages
   that would answer the query, each covering a different aspect or using
   different wording.
2. Each passage is embedded **at query time** independently.
3. The original query is also embedded and included in the search for recall.
4. All result lists are merged via RRF fusion.
5. Retrieval finds chunks whose vectors are near any of the hypothetical
   passages or the original query.

**When to use:** Almost always. HyDE is the default enhancement for slow and
global tiers. It consistently improves recall for factual and descriptive
queries.

**Trade-off:** One extra LLM call per search (returns N documents). O(N + 1)
embedding + retrieval operations. Configure N via `HYDE_N_DOCUMENTS` (default 2).

**Important:** HyDE is a **query-time** technique only. Do not confuse it with
**Hypothetical Questions** (index-time), which pre-generates question vectors
during ingestion.

---

### Sub-Queries — Query-time

**Key insight:** Complex questions ("compare transformer and RNN performance on
long sequences") have multiple aspects. A single embedding may not surface
documents that address each aspect. By decomposing the query into independent
atomic sub-questions, each can independently retrieve its own relevant context.

**Architecture diagram (Sub-query RAG Workflow):**
```
                    ┌──────────────┐
                    │   Query      │
                    │  (complex)   │
                    └──────┬───────┘
                           │ ② LLM decomposes
                           ▼
              ┌────────────┼────────────┐
              ▼            ▼            │
       ┌──────────┐ ┌──────────┐       │
       │sub query1│ │sub query2│       │ original
       └─────┬────┘ └─────┬────┘       │
             │ ③ embed    │            │
             ▼   + search ▼            ▼
         ┌──────────────────────────┐
         │ Vector Store:            │
         │ ┌─[hit 1]─────────────┐ │
         │ └─────────────────────┘ │
         │ ┌─[hit 2]─────────────┐ │
         │ └─────────────────────┘ │
         │ ┌─────────────────────┐ │ ④ parallel lookups
         │ └─────────────────────┘ │
         └────────┬────────────────┘
                  │ ⑤ gather top-k
          ┌───────┴────────┐
          ▼                ▼
   ┌──────────────┐ ┌──────────────┐
   │ top-k chunk 1│ │ top-k chunk 2│
   └──────┬───────┘ └──────┬───────┘
          │ ⑥ merged       │
          └────────┬────────┘
                   ▼
            ┌──────────┐
            │   LLM    │
            └────┬─────┘
                 │ ⑦ synthesize
                 ▼
            ┌──────────┐
            │  Answer  │
            └──────────┘
```

**How it works:**
1. The LLM analyzes whether the query needs decomposition:
   - **Simple query** ("how does gradient descent work?") → returned as-is.
   - **Complex / multi-faceted query** ("compare Milvus and Zilliz Cloud")
     → decomposed into N simpler sub-questions (up to `SUB_QUERIES_COUNT`,
     default 2).
2. The original query is always appended for recall.
3. Each sub-question is embedded and searched **independently** against the
   vector store — performing separate semantic lookups.
4. The independent vector lookups return their respective top-K relevant chunks
   (isolated context buckets per sub-query).
5. All result lists (sub-query hits + original query hits) are fused via RRF
   and re-scored by the reranker.
6. The merged context is synthesized by the final LLM into a cohesive answer.

**Example — complex query decomposition:**
```
User query: "What are the differences in features between Milvus and Zilliz Cloud?"
                     │
                     ▼  (LLM decides to decompose)
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
  Sub-query 1:           Sub-query 2:
  "What are the          "What are the
  features of            features of
  Milvus?"               Zilliz Cloud?"
```

**When to use:** Multi-aspect, comparative, or open-ended questions. Global tier
only by default.

**Trade-off:** O(N + 1) embedding + retrieval calls (N sub-queries + original
query). Configurable via `SUB_QUERIES_COUNT`.

---

### Stepback (Take-a-Step-Back) — Query-time

**Key insight:** When a user asks a highly specific, complex, or concrete
question ("what is the learning rate in the paper on page 7?"), directly
querying a vector database often surfaces shallow matches or misses the
underlying principles needed to answer correctly. A broader, high-level
question ("what optimisation techniques are used in transformer training?")
surfaces the foundational knowledge. The system retrieves context with this
abstracted question and then combines both sources for a grounded answer.

**Architecture diagram (Stepback Prompting RAG Workflow):**
```
                    ┌───────────────────┐
                    │  Original Query   │
                    │  (very specific)  │
                    └────────┬──────────┘
                             │ ② LLM abstracts
                             ▼
                    ┌───────────────────┐
                    │  Stepback Query   │
                    │(fundamental       │
                    │ concepts / rules) │
                    └────────┬──────────┘
                             │ ③ embed + search
                             ▼
                    ┌────────────────────┐
                    │ Vector Store:      │
                    │ ┌───────────────┐  │
                    │ │ [hit]         │  │
                    │ └───────────────┘  │
                    │ ┌───────────────┐  │ ④ surface
                    │ │[foundational] │  │    closest match
                    │ └───────────────┘  │
                    │ ┌───────────────┐  │
                    │ └───────────────┘  │
                    └────────┬───────────┘
                             │ ⑤ top-k chunks
                             ▼
                    ┌───────────────────┐
                    │  Top K Chunks     │
                    │(broad /           │
                    │ foundational)     │
                    └────────┬──────────┘
                             │ ⑥ LLM generates
                             ▼
                    ┌───────────────────┐
                    │ Stepback Answer   │
                    │(rules / context)  │
                    └────────┬──────────┘
                             │
              ┌──────────────┼──────────────┐
              │ ⑦ original   │              │
              │    query      │ ⑦ stepback   │
              │   (specific)  │    answer    │
              │              │  (foundation) │
              └──────┬───────┴──────┬───────┘
                     │              │
                     ▼              ▼
                ┌────────────────────────┐
                │     Final LLM          │
                │   (synthesis stage)    │ ⑧
                └───────────┬────────────┘
                            ▼
                   ┌────────────────┐
                   │   Final Answer │
                   │   (grounded)   │
                   └────────────────┘
```

**How it works:**
1. The LLM abstracts the highly specific query into a broader **stepback
   question** targeting fundamental concepts, principles, rules, or constraints
   (step ②).
2. The stepback question is embedded and searched against the vector store,
   surfacing chunks containing foundational knowledge (steps ③-④).
3. Those chunks are gathered as the top-k relevant context (step ⑤).
4. The retrieved chunks are fed to an LLM which generates a comprehensive,
   high-level **stepback answer** detailing the rules, limitations, or
   background context (step ⑥). This happens in the search pipeline or
   downstream chatbot-service.
5. Both the original query (specific details) and the stepback answer
   (foundational rules) are routed to the final LLM for synthesis (steps ⑦-⑧).
6. Inside the RAG pipeline, the stepback question is always searched alongside
   the original query for recall; results are fused via RRF and re-scored by
   the reranker using the original query.

**When to use:** Narrow, specific queries where background context or
foundational knowledge is needed. Global tier only by default.

**Trade-off:** One extra LLM call per search. Less useful for already-broad
queries.

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
| What is generated | N hypothetical *documents* (answer query) | Hypothetical *questions* (that chunks answer) |
| What gets embedded | Each generated document text (N+1 with original query) | The generated question text |
| Where stored | Not stored — generated fresh each query | Stored in Milvus as persistent vectors |
| Search target | Document chunk vectors (doc-to-doc) | Question vectors + query-to-query resolution |
| Configuration | `QUERY_ENHANCEMENTS=hyde` + `HYDE_N_DOCUMENTS=N` | `HYPOTHETICAL_QUESTIONS_PER_CHUNK=N` |

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
  ├── hyde → [hyde_passage_1, hyde_passage_2, ..., original]
  ├── sub_queries → [sub_q1, sub_q2, ..., original]
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

| Enhancement | Extra LLM calls | Embedding/retrieval operations | Latency overhead |
|-------------|----------------|-------------------------------|-----------------|
| none | 0 | 1 | 0 ms |
| hyde (N=2) | 1 (returns N docs) | N + 1 (hyde docs + original) | ~200-500 ms + 2-3× retrieval |
| sub_queries | 1 (returns 2-4 Qs) | 2-5 (sub-Qs + original) | ~300-600 ms + 2-4× retrieval |
| stepback | 1 (returns 1 Q) | 2 (stepback + original) | ~200-400 ms + 1× retrieval |
| all three | 3 | N + 4+ | ~800 ms - 1.8 s + extra retrievals |
| hypothetical questions (index-time) | N×Q at ingest | 0 ms at query time (vectors precomputed) |

Latency is highly dependent on the LLM endpoint; these are rough estimates for a
local vLLM serving a 7B model.
