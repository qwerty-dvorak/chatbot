"""
OpenAI-compatible API for nvidia/nemotron-colembed-vl-8b-v2.

Multimodal late-interaction embedding model.
Text queries and document images produce ColBERT-style multi-vector
representations. Returns per-token embedding vectors (hidden dim: 4096).
"""

import base64
import io
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

MODEL_NAME = os.environ.get(
    "COLEMBED_MODEL", "nvidia/nemotron-colembed-vl-8b-v2"
)
DEVICE = os.environ.get("DEVICE", "cuda")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8003"))
DTYPE = getattr(torch, os.environ.get("DTYPE", "bfloat16"))

print(f"Loading {MODEL_NAME}...", file=sys.stderr)
model = AutoModel.from_pretrained(
    MODEL_NAME,
    trust_remote_code=True,
    torch_dtype=DTYPE,
    device_map="auto",
    attn_implementation="flash_attention_2",
).eval()
processor = AutoProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)
print("Model loaded.", file=sys.stderr)

TEXT_EMBEDDING_DIM = 4096


class ColEmbedHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        if self.path == "/v1/embeddings":
            result = self._handle_embed(body)
        else:
            result = self._handle_forward(body)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_embed(self, body):
        input_data = body.get("input", [])
        if isinstance(input_data, str):
            input_data = [input_data]

        emb_type = body.get("type", "query")
        all_embeddings = []

        for item in input_data:
            if emb_type == "query":
                inputs = processor(text=item, return_tensors="pt").to(model.device)
            else:
                inputs = processor(text=item, return_tensors="pt").to(model.device)

            with torch.inference_mode():
                output = model(**inputs)

            emb = output.last_hidden_state.mean(dim=1).squeeze().cpu().tolist()
            all_embeddings.append(emb)

        return {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": emb}
                for i, emb in enumerate(all_embeddings)
            ],
            "model": MODEL_NAME,
            "dimension": TEXT_EMBEDDING_DIM,
        }

    def _handle_forward(self, body):
        image_data = body.get("image")
        if image_data and "," in image_data:
            image_data = image_data.split(",", 1)[1]

        image = Image.open(io.BytesIO(base64.b64decode(image_data)))
        text = body.get("text", "")

        if image:
            inputs = processor(images=image, return_tensors="pt").to(model.device)
            with torch.inference_mode():
                image_emb = model.forward_images(**inputs)
            return {
                "embedding_shape": list(image_emb.shape),
                "embedding": image_emb.mean(dim=1).squeeze().cpu().tolist(),
            }

        inputs = processor(text=text, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            text_emb = model(**inputs)
        emb = text_emb.last_hidden_state.mean(dim=1).squeeze().cpu().tolist()
        return {"embedding": emb, "dimension": TEXT_EMBEDDING_DIM}

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
    server = HTTPServer((HOST, PORT), ColEmbedHandler)
    print(f"Serving on {HOST}:{PORT}", file=sys.stderr)
    server.serve_forever()
