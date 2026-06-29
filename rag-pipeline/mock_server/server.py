"""Stdlib-only OpenAI-compatible mock server for RAG pipeline local development.

Runs 5 HTTP servers in separate threads:
  9000 — chat completions
  9001 — text embeddings (/v1/embeddings)
  9002 — multimodal embeddings (/pooling)
  9003 — reranker (/score)
  9004 — OCR (/v1/chat/completions with image → extracted text)
"""

import argparse
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
    rng = random.Random(hash(text) & 0x7FFFFFFF)  # noqa: S311
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    magnitude = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / magnitude for v in vec]


def _fake_rerank_score(query: str, doc: str) -> float:
    """Return a deterministic score in [0, 1] for a (query, doc) pair."""
    return (hash(query + doc) & 0xFFFF) / 0xFFFF


def _sse(data: dict) -> bytes:
    """Encode a single SSE frame."""
    return ("data: " + json.dumps(data) + "\n\n").encode()


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    """Read and JSON-parse the request body from *handler*."""
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw)


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
    handler.send_header("Transfer-Encoding", "chunked")
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
            _send_json(
                self,
                {
                    "object": "list",
                    "data": [{"id": self.model_name, "object": "model"}],
                },
            )
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):  # noqa: C901, PLR0912
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
        use_tool_call = bool(tools) and any(kw in last_user.lower() for kw in search_keywords)

        call_id = "call_" + uuid.uuid4().hex[:16]
        tool_name = tools[0]["function"]["name"] if tools else "search"
        tool_args = json.dumps({"query": last_user[:120]})

        if stream:
            _send_sse_headers(self)
            ts = int(time.time())
            chunk_id = "chatcmpl-" + uuid.uuid4().hex[:20]

            if use_tool_call:
                # First delta: role + tool_call header (id + name, empty args)
                self.wfile.write(
                    _sse(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": ts,
                            "model": self.model_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {"name": tool_name, "arguments": ""},
                                            }
                                        ],
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                )
                # Subsequent deltas: argument chunks
                for chunk in [tool_args[: len(tool_args) // 2], tool_args[len(tool_args) // 2 :]]:
                    self.wfile.write(
                        _sse(
                            {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": ts,
                                "model": self.model_name,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": 0,
                                                    "function": {"arguments": chunk},
                                                }
                                            ],
                                        },
                                        "finish_reason": None,
                                    }
                                ],
                            }
                        )
                    )
                # Final delta: finish_reason
                self.wfile.write(
                    _sse(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": ts,
                            "model": self.model_name,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                        }
                    )
                )
            else:
                reply = f"Mock reply to: {last_user[:80]}" if last_user else "Mock reply."
                words = reply.split()
                # Role delta
                self.wfile.write(
                    _sse(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": ts,
                            "model": self.model_name,
                            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                        }
                    )
                )
                # Content deltas (word by word)
                for i, word in enumerate(words):
                    text = word if i == 0 else " " + word
                    self.wfile.write(
                        _sse(
                            {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": ts,
                                "model": self.model_name,
                                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                            }
                        )
                    )
                # Final delta
                self.wfile.write(
                    _sse(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": ts,
                            "model": self.model_name,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                    )
                )

            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        else:
            ts = int(time.time())
            if use_tool_call:
                _send_json(
                    self,
                    {
                        "id": "chatcmpl-" + uuid.uuid4().hex[:20],
                        "object": "chat.completion",
                        "created": ts,
                        "model": self.model_name,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": call_id,
                                            "type": "function",
                                            "function": {"name": tool_name, "arguments": tool_args},
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                    },
                )
            else:
                reply = f"Mock reply to: {last_user[:80]}" if last_user else "Mock reply."
                _send_json(
                    self,
                    {
                        "id": "chatcmpl-" + uuid.uuid4().hex[:20],
                        "object": "chat.completion",
                        "created": ts,
                        "model": self.model_name,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": reply},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                    },
                )


# ---------------------------------------------------------------------------
# Embeddings handler
# ---------------------------------------------------------------------------


class EmbeddingsHandler(BaseHTTPRequestHandler):
    model_name: str = "mock-text-embed"
    dim: int = 768

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/models":
            _send_json(
                self,
                {
                    "object": "list",
                    "data": [{"id": self.model_name, "object": "model"}],
                },
            )
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/v1/embeddings":
            self._handle_embeddings()
        elif self.path == "/pooling":
            self._handle_pooling()
        else:
            _send_json(self, {"error": "not found"}, 404)

    def _handle_embeddings(self) -> None:
        body = _read_body(self)
        inp = body.get("input", [])
        if isinstance(inp, str):
            inp = [inp]

        data = []
        total_tokens = 0
        for i, text in enumerate(inp):
            embedding = _fake_embedding(text, self.dim)
            data.append({"object": "embedding", "index": i, "embedding": embedding})
            total_tokens += max(1, len(text.split()))

        _send_json(
            self,
            {
                "object": "list",
                "data": data,
                "model": self.model_name,
                "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
            },
        )

    def _handle_pooling(self) -> None:
        body = _read_body(self)
        inp = body.get("input", "")
        # Handle multi_modal_data (image-only input format)
        mm_data = body.get("multi_modal_data", {})
        if mm_data.get("image"):
            inp = f"image:{len(mm_data['image'][0])}"  # deterministic key from image size
        elif isinstance(inp, list):
            inp = " ".join(str(p) for p in inp)

        tok_count = max(2, len(str(inp).split()))
        vectors = [_fake_embedding(str(inp), self.dim) for _ in range(tok_count)]

        _send_json(
            self,
            {
                "id": "pool-mock",
                "object": "list",
                "data": [{"index": 0, "object": "pooling", "data": vectors}],
                "model": self.model_name,
                "usage": {"prompt_tokens": tok_count, "total_tokens": tok_count},
            },
        )


# ---------------------------------------------------------------------------
# Reranker handler
# ---------------------------------------------------------------------------


class RerankerHandler(BaseHTTPRequestHandler):
    model_name: str = "mock-reranker"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path == "/score":
            self._handle_score()
        else:
            _send_json(self, {"error": "not found"}, 404)

    def _handle_score(self) -> None:
        body = _read_body(self)

        # Batch format: queries[] + documents[] (1-to-1 pairs)
        queries = body.get("queries")
        documents = body.get("documents")

        # Single-pair fallback: text_1 + text_2
        if queries is None:
            text_1 = body.get("text_1", "")
            text_2 = body.get("text_2", "")
            queries = [text_1] if isinstance(text_1, str) else text_1
            documents = [text_2] if isinstance(text_2, str) else text_2

        data = []
        total_tokens = 0
        for i, (q, doc) in enumerate(zip(queries or [], documents or [], strict=False)):
            score = _fake_rerank_score(q, doc)
            data.append({"index": i, "object": "score", "score": score})
            total_tokens += max(1, len(q.split())) + max(1, len(doc.split()))

        _send_json(
            self,
            {
                "id": "score-mock",
                "object": "list",
                "data": data,
                "model": self.model_name,
                "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
            },
        )


# ---------------------------------------------------------------------------
# OCR handler (PaddleOCR-VL mock — returns placeholder text from image)
# ---------------------------------------------------------------------------


class OCRHandler(BaseHTTPRequestHandler):
    model_name: str = "PaddlePaddle/PaddleOCR-VL-1.6"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/health", "/v1/models"):
            _send_json(self, {"status": "ok"})
        else:
            _send_json(self, {"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            body = _read_body(self)
            messages = body.get("messages", [])
            # Extract image count from content array for determinism
            image_count = 0
            for msg in messages:
                content = msg.get("content", [])
                if isinstance(content, list):
                    image_count += sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
            reply = f"[mock OCR output — {image_count} image(s) received. This is placeholder extracted text.]"
            ts = int(time.time())
            _send_json(
                self,
                {
                    "id": "chatcmpl-" + uuid.uuid4().hex[:20],
                    "object": "chat.completion",
                    "created": ts,
                    "model": self.model_name,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 256, "completion_tokens": len(reply.split()), "total_tokens": 256 + len(reply.split())},
                },
            )
        else:
            _send_json(self, {"error": "not found"}, 404)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _make_chat_handler(model_name: str) -> type:
    return type("_ChatHandler", (ChatHandler,), {"model_name": model_name})


def _make_embed_handler(model_name: str, dim: int) -> type:
    return type("_EmbedHandler", (EmbeddingsHandler,), {"model_name": model_name, "dim": dim})


def _make_rerank_handler(model_name: str) -> type:
    return type("_RerankerHandler", (RerankerHandler,), {"model_name": model_name})


def _make_ocr_handler(model_name: str) -> type:
    return type("_OCRHandler", (OCRHandler,), {"model_name": model_name})


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
    parser.add_argument("--embed-dim", type=int, default=768)
    parser.add_argument("--multimodal-dim", type=int, default=512)
    parser.add_argument("--chat-model", default="mock-chat")
    parser.add_argument("--embed-model", default="mock-text-embed")
    parser.add_argument("--multimodal-model", default="mock-multimodal-embed")
    parser.add_argument("--rerank-model", default="mock-reranker")
    parser.add_argument("--ocr-model", default="PaddlePaddle/PaddleOCR-VL-1.6")
    args = parser.parse_args()

    servers = [
        (_make_chat_handler(args.chat_model), args.chat_port),
        (_make_embed_handler(args.embed_model, args.embed_dim), args.embed_port),
        (_make_embed_handler(args.multimodal_model, args.multimodal_dim), args.multimodal_port),
        (_make_rerank_handler(args.rerank_model), args.rerank_port),
        (_make_ocr_handler(args.ocr_model), args.ocr_port),
    ]

    for handler_cls, port in servers:
        t = threading.Thread(target=_start_server, args=(handler_cls, port), daemon=True)
        t.start()

    print(f"Chat completions  → http://localhost:{args.chat_port}/v1/chat/completions")  # noqa: T201
    print(f"Text embeddings   → http://localhost:{args.embed_port}/v1/embeddings")  # noqa: T201
    print(f"Multimodal embed  → http://localhost:{args.multimodal_port}/pooling")  # noqa: T201
    print(f"Reranker          → http://localhost:{args.rerank_port}/score")  # noqa: T201
    print(f"OCR               → http://localhost:{args.ocr_port}/v1/chat/completions")  # noqa: T201
    print("Press Ctrl+C to stop.")  # noqa: T201

    threading.Event().wait()


if __name__ == "__main__":
    main()
