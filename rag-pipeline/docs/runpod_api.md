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

**Model:** `Qwen/Qwen3-VL-Reranker-8B`
**Endpoint:** `POST /pooling`

> With `--runner pooling`, the reranker exposes the **/pooling** endpoint (same as
> the multimodal embed). Both pods share the same API shape. The `--hf-overrides`
> flag configures the model architecture to properly load the classification head.

### Request

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-8B",
  "input": "What is the capital of France? Paris is the capital of France."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | yes | Model ID |
| `input` | string or string[] | yes | Text to embed (pair for cross-encoding) |

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
        [0.0012, -0.0034, ...],
        [0.0056, 0.0012, ...]
      ]
    }
  ],
  "model": "Qwen/Qwen3-VL-Reranker-8B",
  "usage": {
    "prompt_tokens": 12,
    "total_tokens": 12
  }
}
```

| Field | Value |
|-------|-------|
| Output type | ColBERT-style multi-vector pooling |
| Each vector dim | **4096** |
| Data type | `float32` |
| Max tokens | 4096 |

### vLLM Config

```
--model Qwen/Qwen3-VL-Reranker-8B
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
