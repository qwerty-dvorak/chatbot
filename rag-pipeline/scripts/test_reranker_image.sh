#!/bin/bash
# Test reranker API (multimodal: text + image)
# POST /score

URL="${URL:-https://nqg53nubtze0xh-8000.proxy.runpod.net}"
MODEL="${MODEL:-Qwen/Qwen3-VL-Reranker-2B}"

curl -s -X POST "$URL/score" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"text_1\": \"A woman with a dog on a beach\",
    \"text_2\": {
      \"content\": [
        {\"type\": \"text\", \"text\": \"A woman shares a joyful moment with her golden retriever on a sun-drenched beach at sunset.\"},
        {\"type\": \"image_url\", \"image_url\": {\"url\": \"https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen-VL/assets/demo.jpeg\"}}
      ]
    }
  }" | python3 -m json.tool
