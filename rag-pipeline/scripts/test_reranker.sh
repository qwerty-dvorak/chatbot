#!/bin/bash
# Test reranker API (text-only)
# POST /score

URL="${URL:-https://nqg53nubtze0xh-8000.proxy.runpod.net}"
MODEL="${MODEL:-Qwen/Qwen3-VL-Reranker-2B}"

curl -s -X POST "$URL/score" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"text_1\": \"what is the capital of france\",
    \"text_2\": \"Paris is the capital of France\"
  }" | python3 -m json.tool
