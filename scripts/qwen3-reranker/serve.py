"""
OpenAI-compatible reranker API for Qwen3-VL-Reranker-8B.

Cross-encoder reranker that scores query-document pairs.
Outputs relevance scores (not embeddings).
"""

import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

import torch
from transformers import AutoModelForSequenceClassification, AutoProcessor

MODEL_NAME = os.environ.get(
    "RERANKER_MODEL", "Qwen/Qwen3-VL-Reranker-8B"
)
DEVICE = os.environ.get("DEVICE", "cuda")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8004"))
DTYPE = getattr(torch, os.environ.get("DTYPE", "bfloat16"))

print(f"Loading {MODEL_NAME}...", file=sys.stderr)
model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    trust_remote_code=True,
    torch_dtype=DTYPE,
    device_map="auto",
    attn_implementation="flash_attention_2",
).eval()
processor = AutoProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)
print("Model loaded.", file=sys.stderr)


class RerankerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        query = body.get("query", "")
        documents = body.get("documents", [])
        if isinstance(documents, str):
            documents = [documents]

        pairs = [[query, doc] for doc in documents]

        inputs = processor(
            text=pairs,
            padding=True,
            truncation=True,
            return_tensors="pt",
        ).to(model.device)

        with torch.inference_mode():
            outputs = model(**inputs)
            scores = outputs.logits.squeeze(-1).cpu().tolist()

        if isinstance(scores, float):
            scores = [scores]

        results = [
            {"index": i, "score": round(s, 6), "document": documents[i]}
            for i, s in enumerate(scores)
        ]
        results.sort(key=lambda x: x["score"], reverse=True)

        data = {
            "object": "list",
            "model": MODEL_NAME,
            "results": results,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return
        if self.path == "/v1/models":
            data = {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}", file=sys.stderr)


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), RerankerHandler)
    print(f"Serving on {HOST}:{PORT}", file=sys.stderr)
    server.serve_forever()
