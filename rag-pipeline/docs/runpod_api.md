# RunPod GPU API Endpoints

Three vLLM servers deployed on RunPod for RAG pipeline:

| Service | Pod ID | GPU | Cost/hr |
|---------|--------|-----|---------|
| Text Embedding | `jio9vdwk5an2ot` | NVIDIA L4 (24GB) | $0.39 |
| Multimodal Embedding | `n90g9jigviyth3` | NVIDIA H100 (80GB) | $3.29 |
| Reranker | `nqg53nubtze0xh` | NVIDIA H100 (80GB) | $3.29 |

Total: **$6.97/hr**

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

### Script

```bash
bash rag-pipeline/scripts/test_text_embed.sh
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
--tensor-parallel-size 1
--gpu-memory-utilization 0.90
--max-model-len 8192
--limit-mm-per-prompt {"image": 1, "video": 0}
--skip-mm-profiling
--runner pooling
```

### Script

```bash
bash rag-pipeline/scripts/test_mm_embed.sh
```

---

## 3. Reranker

**Model:** `Qwen/Qwen3-VL-Reranker-2B`
**Endpoint:** `POST /score`
**URL:** `https://nqg53nubtze0xh-8000.proxy.runpod.net`

#### Text-only (single score)

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "text_1": "what is the capital of france",
  "text_2": "Paris is the capital of France"
}
```

#### Batch scoring

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "queries": ["what is the capital of france", "who wrote 1984"],
  "documents": ["Paris is the capital of France", "George Orwell"]
}
```

#### Multimodal (text + image)

```json
{
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "text_1": "A woman with a dog on a beach",
  "text_2": {
    "content": [
      {"type": "text", "text": "A woman shares a joyful moment with her golden retriever on a sun-drenched beach at sunset."},
      {"type": "image_url", "image_url": {"url": "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen-VL/assets/demo.jpeg"}}
    ]
  }
}
```

### Response

```json
{
  "id": "score-...",
  "object": "list",
  "data": [
    {
      "index": 0,
      "object": "score",
      "score": 0.979
    }
  ],
  "model": "Qwen/Qwen3-VL-Reranker-2B",
  "usage": {
    "prompt_tokens": 12,
    "total_tokens": 12
  }
}
```

| Field | Value |
|-------|-------|
| Score range | 0.0 – 1.0 (higher = more relevant) |
| Input modes | text-only, text+image, text only doc, image only doc |
| Max tokens | depends on model config |
| Task | `score` (via `--hf-overrides`) |

### vLLM Config

```
--model Qwen/Qwen3-VL-Reranker-2B
--trust-remote-code
--tensor-parallel-size 1
--gpu-memory-utilization 0.90
--port 8000
--hf-overrides '{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'
```

### Scripts

```bash
# Text-only
bash rag-pipeline/scripts/test_reranker.sh

# Multimodal (text + image)
bash rag-pipeline/scripts/test_reranker_image.sh
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
