"""
OpenAI-compatible embedding API for nvidia/llama-embed-nemotron-8b.

Text embedding dimension: 4096
"""

import json
import os
import sys

from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer
from http.server import HTTPServer, BaseHTTPRequestHandler

MODEL_NAME = os.environ.get("EMBED_MODEL", "nvidia/llama-embed-nemotron-8b")
DEVICE = os.environ.get("DEVICE", "cuda")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8002"))
DTYPE = os.environ.get("DTYPE", "bfloat16")
ATTRN_IMPL = os.environ.get("ATTRN_IMPL", "eager")

print(f"Loading {MODEL_NAME}...", file=sys.stderr)
model = SentenceTransformer(
    MODEL_NAME,
    trust_remote_code=True,
    device=DEVICE,
    model_kwargs={
        "attn_implementation": ATTRN_IMPL,
        "torch_dtype": DTYPE,
    },
    tokenizer_kwargs={"padding_side": "left"},
)
print("Model loaded.", file=sys.stderr)


class EmbeddingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        input_texts = body.get("input", [])
        if isinstance(input_texts, str):
            input_texts = [input_texts]

        emb_type = body.get("type", "query")
        if emb_type == "query":
            embeddings = model.encode_query(input_texts)
        elif emb_type == "document":
            embeddings = model.encode_document(input_texts)
        else:
            embeddings = model.encode(input_texts)

        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()

        data = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": emb}
                for i, emb in enumerate(embeddings)
            ],
            "model": MODEL_NAME,
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
    server = HTTPServer((HOST, PORT), EmbeddingHandler)
    print(f"Serving on {HOST}:{PORT}", file=sys.stderr)
    server.serve_forever()
