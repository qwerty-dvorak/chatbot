#!/bin/bash
# Test multimodal embedding API
# POST /pooling

URL="${URL:-https://n90g9jigviyth3-8000.proxy.runpod.net}"
MODEL="${MODEL:-nvidia/nemotron-colembed-vl-8b-v2}"

curl -s -X POST "$URL/pooling" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"input\": \"hello world\"
  }" | python3 -c "
import sys, json
d = json.load(sys.stdin)
e = d['data'][0]['data']
print(f'Vectors: {len(e)}, Dim: {len(e[0])}')
print(f'First 5: {e[0][:5]}')
print(f'Model: {d[\"model\"]}')
"
