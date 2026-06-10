#!/bin/bash
# Test text embedding API
# POST /v1/embeddings

URL="${URL:-https://jio9vdwk5an2ot-8000.proxy.runpod.net}"
MODEL="${MODEL:-nvidia/llama-embed-nemotron-8b}"

curl -s -X POST "$URL/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"input\": \"hello world\"
  }" | python3 -m json.tool
