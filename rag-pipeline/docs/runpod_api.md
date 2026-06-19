# RunPod GPU API Endpoints

Three vLLM servers deployed on RunPod for RAG pipeline:

| Service | GPU | Cost/hr |
|---------|-----|---------|
| Text Embedding | NVIDIA GeForce RTX 3090 | $0.22 |
| Multimodal Embedding | NVIDIA GeForce RTX 3090 | $0.22 |
| Reranker | NVIDIA GeForce RTX 3090 | $0.22 |

Total: **$0.66/hr**

---

## 1. Text Embedding

**Model:** `nvidia/llama-embed-nemotron-8b`
**Endpoint:** `POST /v1/embeddings`
**URL:** `https://jio9vdwk5an2ot-8000.proxy.runpod.net`

### Request

```json
{
  "model": "nvidia/llama-embed-nemotron-8b",
  "input": "hello world"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | yes | Model ID |
| `input` | string or string[] | yes | Text to embed |

### Response

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [-0.0085, -0.0031, 0.0031, ...]
    }
  ],
  "model": "nvidia/llama-embed-nemotron-8b",
  "usage": {
    "prompt_tokens": 3,
    "total_tokens": 3
  }
}
```

| Field | Value |
|-------|-------|
| Embedding dimension | **4096** (single vector per input) |
| Data type | `float32` |
| Max tokens | 8192 |

### Test

```bash
bash rag-pipeline/test_runpod_endpoints.sh
```

---

## 2. Multimodal Embedding

**Model:** `nvidia/nemotron-colembed-vl-8b-v2`
**Endpoint:** `POST /pooling`
**URL:** `https://n90g9jigviyth3-8000.proxy.runpod.net`

> Uses `--runner pooling` — embedding endpoint is `/pooling`, NOT `/v1/embeddings`.

### Request

```json
{
  "model": "nvidia/nemotron-colembed-vl-8b-v2",
  "input": "hello world"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | yes | Model ID |
| `input` | string or string[] | yes | Text to embed |

### Response

```json
{
  "id": "pool-...",
  "object": "list",
  "data": [
    {
      "index": 0,
      "object": "pooling",
      "data": [
        [0.0012, -0.0034, ...],   // token 1 embedding (4096-dim)
        [0.0056, 0.0012, ...]     // token 2 embedding (4096-dim)
      ]
    }
  ],
  "model": "nvidia/nemotron-colembed-vl-8b-v2",
  "usage": {
    "prompt_tokens": 2,
    "total_tokens": 2
  }
}
```

| Field | Value |
|-------|-------|
| Embedding type | **ColBERT-style multi-vector** (late interaction) |
| Vectors per input | **2** (per 2 tokens) |
| Each vector dim | **4096** |
| Data type | `float32` |
| Max tokens | 8192 |

### vLLM Config

```
--model nvidia/nemotron-colembed-vl-8b-v2
--trust-remote-code
--runner pooling
--gpu-memory-utilization 0.90
--max-model-len 8192
--limit-mm-per-prompt <MM_LIMIT env var>
--skip-mm-profiling
```

### Test

```bash
bash rag-pipeline/test_runpod_endpoints.sh
```

---

## 3. Reranker

**Model:** `Qwen/Qwen3-VL-Reranker-2B`
**Endpoints:** `POST /score`, `POST /v1/score`, `POST /v1/rerank`, `POST /pooling`
**URL:** `https://lwq7azeh2cl1xf-8000.proxy.runpod.net`

> With `--runner pooling`, the reranker exposes multiple scoring endpoints.
> The **preferred** endpoints are `/score` (structured pairwise) and `/v1/rerank`
> (Cohere-compatible). `/pooling` also works but returns a less structured response.
> The `--hf-overrides` flag configures the model architecture to load the
> classification head.

---

### 3a. POST /score — structured pairwise scoring

**Request formats:**

| Format | Fields | Use case |
|--------|--------|----------|
| ScoreTextRequest | `text_1`, `text_2` | Single query–document pair |
| ScoreQueriesDocumentsRequest | `queries`, `documents` | 1:N or N:N batch |
| ScoreQueriesItemsRequest | `queries`, `items` | Query–short-item batch |
| ScoreDataRequest | `data_1`, `data_2` | Multimodal pairs |

**Single pair (ScoreTextRequest):**

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "text_1": "What is the capital of France?",
  "text_2": "Paris is the capital of France."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | no | Model ID |
| `text_1` | string | **yes** | Query text |
| `text_2` | string | **yes** | Document text |

**Batch 1:N (ScoreQueriesDocumentsRequest):**

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "queries": "What is the capital of France?",
  "documents": [
    "Paris is the capital of France.",
    "London is the capital of UK.",
    "Berlin is the capital of Germany."
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | no | Model ID |
| `queries` | string or string[] | **yes** | Query(s) — 1:N or N:N |
| `documents` | string[] | **yes** | Candidate documents |

**Response:**

```json
{
  "id": "score-91ac2141fb94426d",
  "object": "list",
  "created": 1781155093,
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "data": [
    {
      "index": 0,
      "object": "score",
      "score": 0.998
    }
  ],
  "usage": {
    "prompt_tokens": 14,
    "total_tokens": 14,
    "completion_tokens": 0
  }
}
```

| Field | Value |
|-------|-------|
| Score type | **Scalar relevance score** (0–1, higher = more relevant) |
| Max tokens | 4096 |

---

### 3b. POST /v1/rerank — Cohere-compatible

**Request:**

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "query": "What is the capital of France?",
  "documents": [
    "Paris is the capital of France.",
    "London is the capital of UK.",
    "Berlin is the capital of Germany."
  ],
  "top_n": 3
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | no | Model ID |
| `query` | string | **yes** | Search query |
| `documents` | string[] | **yes** | Documents to rerank |
| `top_n` | int | no | Number of top results (default: all) |

**Response:**

```json
{
  "id": "score-bbcb46f54f5d2476",
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "results": [
    {
      "index": 0,
      "document": {
        "text": "Paris is the capital of France."
      },
      "relevance_score": 0.998
    },
    {
      "index": 2,
      "document": {
        "text": "Berlin is the capital of Germany."
      },
      "relevance_score": 0.961
    },
    {
      "index": 1,
      "document": {
        "text": "London is the capital of UK."
      },
      "relevance_score": 0.957
    }
  ],
  "usage": {
    "prompt_tokens": 42,
    "total_tokens": 42
  }
}
```

> Results are sorted by `relevance_score` descending (most relevant first).

---

### vLLM Config

```
--model Qwen/Qwen3-VL-Reranker-2B
--runner pooling
--trust-remote-code
--gpu-memory-utilization 0.90
--port 8000
--max-model-len 4096
--hf-overrides <HF_OVERRIDES env var>
```

`HF_OVERRIDES` env var:
```json
{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}
```

### Tests

```bash
# All endpoints (text embed, mm embed, reranker)
bash rag-pipeline/test_runpod_endpoints.sh
```

---

## 4. OCR (PaddleOCR-VL-1.6)

**Model:** `PaddlePaddle/PaddleOCR-VL-1.6`
**Endpoint:** `POST /v1/chat/completions`

> Standard OpenAI vision chat completions. Send a rendered page image; model returns extracted text.

### Request

Use task-specific prompts. For PDF text extraction, use `"OCR:"`.

```json
{
  "model": "PaddlePaddle/PaddleOCR-VL-1.6",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,<base64-encoded-png>"
          }
        },
        {
          "type": "text",
          "text": "OCR:"
        }
      ]
    }
  ],
  "temperature": 0.0
}
```

| Task prompt | Use case |
|-------------|----------|
| `"OCR:"` | General text extraction |
| `"Table Recognition:"` | Tables |
| `"Formula Recognition:"` | Math formulas |
| `"Chart Recognition:"` | Charts / graphs |

### Response

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Extracted text from the page..."
      },
      "finish_reason": "stop"
    }
  ]
}
```

### vLLM Config

```
--model PaddlePaddle/PaddleOCR-VL-1.6
--trust-remote-code
--port 8000
--gpu-memory-utilization 0.85
--max-num-batched-tokens 16384
--no-enable-prefix-caching
--mm-processor-cache-gb 0
```

> `--no-enable-prefix-caching` and `--mm-processor-cache-gb 0` are required per official PaddleOCR-VL deployment docs.

### Recommended GPU

NVIDIA RTX A5000 (24GB VRAM) at $0.160/hr — cheapest GPU that reliably runs a 4B VL model.

---

## Cleanup

```bash
runpodctl pod delete jio9vdwk5an2ot n90g9jigviyth3 nqg53nubtze0xh

# Delete all rag- templates
runpodctl template list --all | python3 -c "
import sys,json
[print(t['id']) for t in json.load(sys.stdin) if 'rag-' in t['name']]
" | xargs -r runpodctl template delete
```
## PaddleOCR-VL

RunPod serves `PaddlePaddle/PaddleOCR-VL-1.6` on port 8000 through the vLLM
OpenAI API. Set `OCR_BASE_URL=https://<pod-id>-8000.proxy.runpod.net/v1` and
send image content to `POST /v1/chat/completions` with prompt `OCR:`.
`models/deploy-runpod.sh` provisions this pod. Local mode leaves
`OCR_BASE_URL` empty and uses the installed PaddleOCR runtime.
