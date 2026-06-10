"""
Stdlib-only mock server for the RAG pipeline model APIs.

Runs 5 HTTP servers in separate threads:
  9000 — chat completions
  9001 — text embeddings
  9002 — multimodal pooling
  9003 — reranker scoring
  9004 — OCR chat completions
"""

import argparse
import hashlib
import json
import math
import random
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embedding(text: str, dim: int) -> list[float]:
    """Return a deterministic unit-normalized Gaussian vector for *text*."""
    digest = hashlib.sha256(text.encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    magnitude = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / magnitude for v in vec]


def _fake_rerank_score(query: str, doc: str) -> float:
    """Return a deterministic score in [0, 1] for a (query, doc) pair."""
    digest = hashlib.sha256(f"{query}\0{doc}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _token_count(value) -> int:
    return max(1, len(_as_text(value).split()))


def _sse(data: dict) -> bytes:
    """Encode a single SSE frame."""
    return ("data: " + json.dumps(data) + "\n\n").encode()


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    """Read and JSON-parse the request body from *handler*."""
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return body


def _send_json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_sse_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()


# ---------------------------------------------------------------------------
# Chat completions handler
# ---------------------------------------------------------------------------

class ChatHandler(BaseHTTPRequestHandler):
    model_name: str = "mock-chat"

    def log_message(self, fmt, *args):  # silence default access log
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            _send_json(self, {
                "object": "list",
                "data": [{"id": self.model_name, "object": "model"}],
            })
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            _send_json(self, {"error": "not found"}, 404)
            return

        body = _read_body(self)
        messages = body.get("messages", [])
        tools = body.get("tools", [])
        stream = body.get("stream", False)

        # Find last user message
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            last_user = part.get("text", "")
                            break
                else:
                    last_user = str(content)
                break

        # Decide whether to emit a tool call
        search_keywords = {"search", "find", "retrieve", "lookup", "query"}
        use_tool_call = bool(tools) and any(
            kw in last_user.lower() for kw in search_keywords
        )

        call_id = "call_" + uuid.uuid4().hex[:16]
        tool_name = tools[0]["function"]["name"] if tools else "search"
        tool_args = json.dumps({"query": last_user[:120]})

        if stream:
            _send_sse_headers(self)
            ts = int(time.time())
            chunk_id = "chatcmpl-" + uuid.uuid4().hex[:20]

            if use_tool_call:
                # First delta: role + tool_call header (id + name, empty args)
                self.wfile.write(_sse({
                    "id": chunk_id, "object": "chat.completion.chunk",
                    "created": ts, "model": self.model_name,
                    "choices": [{
                        "index": 0, "delta": {
                            "role": "assistant",
                            "tool_calls": [{
                                "index": 0, "id": call_id, "type": "function",
                                "function": {"name": tool_name, "arguments": ""},
                            }],
                        }, "finish_reason": None,
                    }],
                }))
                # Subsequent deltas: argument chunks
                for chunk in [tool_args[:len(tool_args)//2], tool_args[len(tool_args)//2:]]:
                    self.wfile.write(_sse({
                        "id": chunk_id, "object": "chat.completion.chunk",
                        "created": ts, "model": self.model_name,
                        "choices": [{
                            "index": 0, "delta": {
                                "tool_calls": [{
                                    "index": 0,
                                    "function": {"arguments": chunk},
                                }],
                            }, "finish_reason": None,
                        }],
                    }))
                # Final delta: finish_reason
                self.wfile.write(_sse({
                    "id": chunk_id, "object": "chat.completion.chunk",
                    "created": ts, "model": self.model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }))
            else:
                reply = f"Mock reply to: {last_user[:80]}" if last_user else "Mock reply."
                words = reply.split()
                # Role delta
                self.wfile.write(_sse({
                    "id": chunk_id, "object": "chat.completion.chunk",
                    "created": ts, "model": self.model_name,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                }))
                # Content deltas (word by word)
                for i, word in enumerate(words):
                    text = word if i == 0 else " " + word
                    self.wfile.write(_sse({
                        "id": chunk_id, "object": "chat.completion.chunk",
                        "created": ts, "model": self.model_name,
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                    }))
                # Final delta
                self.wfile.write(_sse({
                    "id": chunk_id, "object": "chat.completion.chunk",
                    "created": ts, "model": self.model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }))

            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        else:
            ts = int(time.time())
            if use_tool_call:
                _send_json(self, {
                    "id": "chatcmpl-" + uuid.uuid4().hex[:20],
                    "object": "chat.completion",
                    "created": ts, "model": self.model_name,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": call_id, "type": "function",
                                "function": {"name": tool_name, "arguments": tool_args},
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                })
            else:
                reply = f"Mock reply to: {last_user[:80]}" if last_user else "Mock reply."
                _send_json(self, {
                    "id": "chatcmpl-" + uuid.uuid4().hex[:20],
                    "object": "chat.completion",
                    "created": ts, "model": self.model_name,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                })


# ---------------------------------------------------------------------------
# Embeddings handler
# ---------------------------------------------------------------------------

class EmbeddingsHandler(BaseHTTPRequestHandler):
    model_name: str = "nvidia/llama-embed-nemotron-8b"
    dim: int = 4096

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            _send_json(self, {
                "object": "list",
                "data": [{"id": self.model_name, "object": "model"}],
            })
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/embeddings":
            _send_json(self, {"error": "not found"}, 404)
            return

        body = _read_body(self)
        inp = body.get("input", [])
        if isinstance(inp, str):
            inp = [inp]
        if not isinstance(inp, list):
            _send_json(self, {"error": "input must be a string or string array"}, 400)
            return

        data = []
        total_tokens = 0
        for i, text in enumerate(inp):
            text = _as_text(text)
            embedding = _fake_embedding(text, self.dim)
            data.append({"object": "embedding", "index": i, "embedding": embedding})
            total_tokens += _token_count(text)

        _send_json(self, {
            "object": "list",
            "data": data,
            "model": body.get("model", self.model_name),
            "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
        })


# ---------------------------------------------------------------------------
# Multimodal pooling handler
# ---------------------------------------------------------------------------

class PoolingHandler(BaseHTTPRequestHandler):
    model_name: str = "nvidia/nemotron-colembed-vl-8b-v2"
    dim: int = 4096

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            _send_json(self, {
                "object": "list",
                "data": [{"id": self.model_name, "object": "model"}],
            })
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/pooling":
            _send_json(self, {"error": "not found"}, 404)
            return

        body = _read_body(self)
        inp = body.get("input", [])
        if isinstance(inp, str) or (
            isinstance(inp, list) and all(isinstance(part, dict) for part in inp)
        ):
            inputs = [inp]
        elif isinstance(inp, list):
            inputs = inp
        else:
            _send_json(self, {"error": "input must be a string or array"}, 400)
            return

        data = []
        total_tokens = 0
        for index, value in enumerate(inputs):
            text = _as_text(value)
            token_count = _token_count(value)
            vectors = [
                _fake_embedding(f"{text}\0token:{token_index}", self.dim)
                for token_index in range(token_count)
            ]
            data.append({
                "index": index,
                "object": "pooling",
                "data": vectors,
            })
            total_tokens += token_count

        _send_json(self, {
            "id": "pool-" + uuid.uuid4().hex[:20],
            "object": "list",
            "data": data,
            "model": body.get("model", self.model_name),
            "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
        })


# ---------------------------------------------------------------------------
# Reranker handler
# ---------------------------------------------------------------------------

class RerankerHandler(BaseHTTPRequestHandler):
    model_name: str = "Qwen/Qwen3-VL-Reranker-2B"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            _send_json(self, {
                "object": "list",
                "data": [{"id": self.model_name, "object": "model"}],
            })
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/score":
            _send_json(self, {"error": "not found"}, 404)
            return

        body = _read_body(self)
        if "queries" in body or "documents" in body:
            queries = body.get("queries", [])
            documents = body.get("documents", [])
        else:
            queries = body.get("text_1", [])
            documents = body.get("text_2", [])

        if not isinstance(queries, list):
            queries = [queries]
        if not isinstance(documents, list):
            documents = [documents]
        if len(queries) == 1 and len(documents) > 1:
            queries *= len(documents)
        if len(documents) == 1 and len(queries) > 1:
            documents *= len(queries)
        if len(queries) != len(documents):
            _send_json(self, {"error": "query and document counts must match"}, 400)
            return

        data = []
        total_tokens = 0
        for index, (query, document) in enumerate(zip(queries, documents)):
            query_text = _as_text(query)
            document_text = _as_text(document)
            data.append({
                "index": index,
                "object": "score",
                "score": _fake_rerank_score(query_text, document_text),
            })
            total_tokens += _token_count(query) + _token_count(document)

        _send_json(self, {
            "id": "score-" + uuid.uuid4().hex[:20],
            "object": "list",
            "data": data,
            "model": body.get("model", self.model_name),
            "usage": {
                "prompt_tokens": total_tokens,
                "total_tokens": total_tokens,
            },
        })


# ---------------------------------------------------------------------------
# OCR handler
# ---------------------------------------------------------------------------

class OcrHandler(BaseHTTPRequestHandler):
    model_name: str = "PaddlePaddle/PaddleOCR-VL-1.6"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            _send_json(self, {
                "object": "list",
                "data": [{"id": self.model_name, "object": "model"}],
            })
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            _send_json(self, {"error": "not found"}, 404)
            return

        body = _read_body(self)
        prompt = "OCR:"
        for message in reversed(body.get("messages", [])):
            for part in message.get("content", []):
                if isinstance(part, dict) and part.get("type") == "text":
                    prompt = part.get("text", prompt)
                    break

        labels = {
            "Table Recognition:": "Mock table recognition output.",
            "Formula Recognition:": "Mock formula recognition output.",
            "Chart Recognition:": "Mock chart recognition output.",
        }
        content = labels.get(prompt, "Mock OCR text extracted from the page.")
        _send_json(self, {
            "id": "chatcmpl-" + uuid.uuid4().hex[:20],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", self.model_name),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 2, "completion_tokens": 8, "total_tokens": 10},
        })


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def _make_chat_handler(model_name: str) -> type:
    return type("_ChatHandler", (ChatHandler,), {"model_name": model_name})


def _make_embed_handler(model_name: str, dim: int) -> type:
    return type("_EmbedHandler", (EmbeddingsHandler,), {"model_name": model_name, "dim": dim})


def _make_pooling_handler(model_name: str, dim: int) -> type:
    return type("_PoolingHandler", (PoolingHandler,), {"model_name": model_name, "dim": dim})


def _make_rerank_handler(model_name: str) -> type:
    return type("_RerankerHandler", (RerankerHandler,), {"model_name": model_name})


def _make_ocr_handler(model_name: str) -> type:
    return type("_OcrHandler", (OcrHandler,), {"model_name": model_name})


def _start_server(handler_class: type, port: int) -> None:
    server = HTTPServer(("", port), handler_class)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Stdlib-only mock OpenAI server for RAG pipeline")
    parser.add_argument("--chat-port", type=int, default=9000)
    parser.add_argument("--embed-port", type=int, default=9001)
    parser.add_argument("--multimodal-port", type=int, default=9002)
    parser.add_argument("--rerank-port", type=int, default=9003)
    parser.add_argument("--ocr-port", type=int, default=9004)
    parser.add_argument("--embed-dim", type=int, default=4096)
    parser.add_argument("--multimodal-dim", type=int, default=4096)
    parser.add_argument("--chat-model", default="mock-chat")
    parser.add_argument("--embed-model", default="nvidia/llama-embed-nemotron-8b")
    parser.add_argument("--multimodal-model", default="nvidia/nemotron-colembed-vl-8b-v2")
    parser.add_argument("--rerank-model", default="Qwen/Qwen3-VL-Reranker-2B")
    parser.add_argument("--ocr-model", default="PaddlePaddle/PaddleOCR-VL-1.6")
    args = parser.parse_args()

    servers = [
        (_make_chat_handler(args.chat_model), args.chat_port),
        (_make_embed_handler(args.embed_model, args.embed_dim), args.embed_port),
        (_make_pooling_handler(args.multimodal_model, args.multimodal_dim), args.multimodal_port),
        (_make_rerank_handler(args.rerank_model), args.rerank_port),
        (_make_ocr_handler(args.ocr_model), args.ocr_port),
    ]

    for handler_cls, port in servers:
        t = threading.Thread(target=_start_server, args=(handler_cls, port), daemon=True)
        t.start()

    print(f"Chat completions  -> http://localhost:{args.chat_port}/v1/chat/completions")
    print(f"Text embeddings   -> http://localhost:{args.embed_port}/v1/embeddings")
    print(f"Multimodal embed  -> http://localhost:{args.multimodal_port}/pooling")
    print(f"Reranker          -> http://localhost:{args.rerank_port}/score")
    print(f"OCR               -> http://localhost:{args.ocr_port}/v1/chat/completions")
    print("Press Ctrl+C to stop.")

    threading.Event().wait()


if __name__ == "__main__":
    main()
