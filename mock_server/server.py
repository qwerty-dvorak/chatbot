#!/usr/bin/env python3
"""
Mock vLLM / OpenAI-compatible server for local development and testing.

Supports:
  - POST /v1/chat/completions  (streaming and non-streaming)
      - reasoning_content deltas (vLLM reasoning format)
      - tool_call deltas when tools are passed in the request
  - POST /v1/embeddings
  - GET  /v1/models

Run:
    python mock_server/server.py                          # chat on :9000, embeddings on :9001
    python mock_server/server.py --chat-port 9000 --embed-port 9001 --embed-dim 4096
    python mock_server/server.py --port 9000              # single port for both
"""

import argparse
import json
import math
import os
import random
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:12]}"


def _tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:8]}"


def _fake_embedding(text: str, dim: int) -> list[float]:
    """Deterministic-ish unit vector based on text hash."""
    seed = hash(text) & 0x7FFFFFFF
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

REASONING_TEXT = (
    "Let me think about this carefully. "
    "The user is asking a question that requires careful analysis. "
    "I will consider the context and provide a well-reasoned answer."
)

ANSWER_TEXT = (
    "Based on my analysis, here is a helpful and accurate response. "
    "I have considered all relevant factors and this is my best answer."
)

TOOL_NAME = "rag.search"
TOOL_ARGS = '{"query": "relevant information", "top_k": 5}'


def _stream_text_response(wfile, model: str, flush):
    cid = _completion_id()

    base = {"id": cid, "object": "chat.completion.chunk", "model": model,
            "choices": [{"index": 0, "finish_reason": None}]}

    # 1. Reasoning content
    for word in REASONING_TEXT.split():
        chunk = {**base, "choices": [{"index": 0, "finish_reason": None,
                                       "delta": {"reasoning_content": word + " "}}]}
        wfile.write(_sse(chunk))
        flush()
        time.sleep(0.01)

    # 2. Answer content
    for word in ANSWER_TEXT.split():
        chunk = {**base, "choices": [{"index": 0, "finish_reason": None,
                                       "delta": {"content": word + " "}}]}
        wfile.write(_sse(chunk))
        flush()
        time.sleep(0.01)

    # 3. Done
    done = {**base, "choices": [{"index": 0, "finish_reason": "stop", "delta": {}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}}
    wfile.write(_sse(done))
    flush()
    wfile.write(b"data: [DONE]\n\n")
    flush()


def _stream_tool_call_response(wfile, model: str, tools: list, flush):
    cid = _completion_id()
    tc_id = _tool_call_id()

    base = {"id": cid, "object": "chat.completion.chunk", "model": model,
            "choices": [{"index": 0, "finish_reason": None}]}

    # Pick the first available tool name from the request if present
    tool_name = TOOL_NAME
    if tools:
        try:
            tool_name = tools[0]["function"]["name"]
        except (KeyError, IndexError):
            pass

    # 1. Reasoning content
    for word in REASONING_TEXT.split():
        chunk = {**base, "choices": [{"index": 0, "finish_reason": None,
                                       "delta": {"reasoning_content": word + " "}}]}
        wfile.write(_sse(chunk))
        flush()
        time.sleep(0.01)

    # 2. Tool call — name first
    name_chunk = {**base, "choices": [{"index": 0, "finish_reason": None, "delta": {
        "tool_calls": [{"index": 0, "id": tc_id, "type": "function",
                        "function": {"name": tool_name, "arguments": ""}}]
    }}]}
    wfile.write(_sse(name_chunk))
    flush()
    time.sleep(0.02)

    # 3. Tool call — arguments streamed in pieces
    for piece in [TOOL_ARGS[:len(TOOL_ARGS)//2], TOOL_ARGS[len(TOOL_ARGS)//2:]]:
        arg_chunk = {**base, "choices": [{"index": 0, "finish_reason": None, "delta": {
            "tool_calls": [{"index": 0, "function": {"arguments": piece}}]
        }}]}
        wfile.write(_sse(arg_chunk))
        flush()
        time.sleep(0.02)

    # 4. finish_reason=tool_calls
    done = {**base, "choices": [{"index": 0, "finish_reason": "tool_calls", "delta": {}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35}}
    wfile.write(_sse(done))
    flush()
    wfile.write(b"data: [DONE]\n\n")
    flush()


def _non_stream_response(model: str, tools: list) -> dict:
    cid = _completion_id()
    if tools:
        tool_name = TOOL_NAME
        try:
            tool_name = tools[0]["function"]["name"]
        except (KeyError, IndexError):
            pass
        return {
            "id": cid, "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "reasoning_content": REASONING_TEXT,
                "tool_calls": [{"id": _tool_call_id(), "type": "function",
                                "function": {"name": tool_name, "arguments": TOOL_ARGS}}],
            }}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
        }
    return {
        "id": cid, "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant",
            "content": ANSWER_TEXT,
            "reasoning_content": REASONING_TEXT,
        }}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class MockHandler(BaseHTTPRequestHandler):
    # Use HTTP/1.0 so streaming responses are delimited by connection-close,
    # avoiding the need to implement HTTP/1.1 chunked transfer encoding.
    protocol_version = "HTTP/1.0"
    embed_dim: int = 4096
    chat_model: str = "mock-chat"
    embed_model: str = "mock-embed"

    def log_message(self, fmt, *args):
        print(f"[mock] {self.address_string()} - {fmt % args}", file=sys.stderr)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/v1/models", "/v1/models/"):
            self._send_json({
                "object": "list",
                "data": [
                    {"id": self.chat_model, "object": "model", "owned_by": "mock"},
                    {"id": self.embed_model, "object": "model", "owned_by": "mock"},
                ],
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/chat/completions":
            self._handle_chat()
        elif path == "/v1/embeddings":
            self._handle_embeddings()
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_chat(self):
        body = self._read_body()
        model = body.get("model", self.chat_model)
        stream = body.get("stream", False)
        tools = body.get("tools", [])

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def flush():
                try:
                    self.wfile.flush()
                except BrokenPipeError:
                    pass

            if tools:
                _stream_tool_call_response(self.wfile, model, tools, flush)
            else:
                _stream_text_response(self.wfile, model, flush)
        else:
            self._send_json(_non_stream_response(model, tools))

    def _handle_embeddings(self):
        body = self._read_body()
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]

        embeddings = [
            {"object": "embedding", "index": i, "embedding": _fake_embedding(t, self.embed_dim)}
            for i, t in enumerate(inputs)
        ]
        total_tokens = sum(len(t.split()) for t in inputs)
        self._send_json({
            "object": "list",
            "model": body.get("model", self.embed_model),
            "data": embeddings,
            "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def make_handler(embed_dim: int, chat_model: str, embed_model: str):
    class H(MockHandler):
        pass
    H.embed_dim = embed_dim
    H.chat_model = chat_model
    H.embed_model = embed_model
    return H


def serve(port: int, handler_class, label: str):
    server = HTTPServer(("0.0.0.0", port), handler_class)
    print(f"[mock] {label} listening on http://0.0.0.0:{port}/v1", file=sys.stderr)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Mock OpenAI-compatible server")
    parser.add_argument("--port", type=int, default=None,
                        help="Single port for both chat and embeddings (overrides --chat-port/--embed-port)")
    parser.add_argument("--chat-port", type=int, default=9000)
    parser.add_argument("--embed-port", type=int, default=9001)
    parser.add_argument("--embed-dim", type=int,
                        default=int(os.environ.get("TEXT_EMBEDDING_DIM", "4096")))
    parser.add_argument("--chat-model", default=os.environ.get("CHAT_MODEL", "mock-chat"))
    parser.add_argument("--embed-model", default=os.environ.get("TEXT_EMBEDDING_MODEL", "mock-embed"))
    args = parser.parse_args()

    handler = make_handler(args.embed_dim, args.chat_model, args.embed_model)

    if args.port is not None:
        # Single port — serve everything together
        serve(args.port, handler, "chat+embeddings")
    else:
        # Two ports — run chat server in a background thread
        t = threading.Thread(
            target=serve,
            args=(args.chat_port, handler, "chat"),
            daemon=True,
        )
        t.start()
        serve(args.embed_port, handler, "embeddings")


if __name__ == "__main__":
    main()
