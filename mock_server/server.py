#!/usr/bin/env python3
"""
Mock vLLM / OpenAI-compatible server for local development and testing.

Supports:
  - POST /v1/chat/completions  (streaming and non-streaming)
      - Picks the right tool from the tools list based on last user message keywords
      - Streams tool_call deltas in proper OpenAI format (id/name on first delta,
        arguments accumulated across subsequent deltas)
      - When last message role is "tool" (continuation after tool execution),
        returns a plain text response referencing the tool result
      - reasoning_content deltas (vLLM reasoning format)
  - POST /v1/embeddings
  - GET  /v1/models

Run:
    python mock_server/server.py                          # chat :9000, embeddings :9001
    python mock_server/server.py --chat-port 9000 --embed-port 9001
    python mock_server/server.py --port 9000              # single port for both
"""

import argparse
import json
import math
import os
import random
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer


# ── helpers ───────────────────────────────────────────────────────────────────

def _sse(data: dict) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()

def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:12]}"

def _tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:8]}"

def _fake_embedding(text: str, dim: int) -> list[float]:
    seed = hash(text) & 0x7FFFFFFF
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ── tool selection logic ───────────────────────────────────────────────────────

# keyword sets → tool name
_TOOL_KEYWORDS: list[tuple[list[str], str]] = [
    (["save memory", "remember that", "note that", "memorize", "store memory"],  "memory.save"),
    (["memory", "recall", "what do you know", "what did i tell"],                "memory.search"),
    (["knowledge base", "search document", "find document", "rag"],              "rag.search"),
    (["compact", "summarize chat", "compress"],                                  "chat.compact"),
    (["ingest status", "ingestion"],                                             "knowledge.ingest_status"),
    (["analyze document", "analyse document", "document analysis"],              "document.analyze"),
]

def _extract_query(text: str) -> str:
    """Pull a short query phrase from the user message."""
    # Remove common preamble words
    cleaned = re.sub(
        r"^(search|find|look up|recall|remember|check|get|show me|tell me about|what do you know about)\s+",
        "", text.strip(), flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(my memory for|the knowledge base for|in memory|about)\b",
        "", cleaned, flags=re.IGNORECASE,
    ).strip()
    return cleaned or text[:80]


def _pick_tool(messages: list[dict], tools: list[dict]) -> tuple[str, dict]:
    """
    Return (tool_name, arguments_dict) by matching last user message against
    tool keyword patterns.  Falls back to the first tool in the list.
    """
    available_names = {
        t.get("function", {}).get("name", "") for t in tools
    }

    # Get last user message text
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content") or ""
            last_user = content.lower() if isinstance(content, str) else ""
            break

    chosen_name = ""
    for keywords, tname in _TOOL_KEYWORDS:
        if tname not in available_names:
            continue
        if any(kw in last_user for kw in keywords):
            chosen_name = tname
            break

    if not chosen_name:
        chosen_name = next(iter(available_names), "") or "unknown"

    query = _extract_query(last_user)

    # Build arguments appropriate for the tool
    if chosen_name == "memory.save":
        args = {"content": query, "importance": 2}
    elif chosen_name == "memory.search":
        args = {"query": query, "top_k": 5}
    elif chosen_name == "rag.search":
        args = {"query": query, "top_k": 5}
    elif chosen_name == "chat.compact":
        args = {}
    elif chosen_name == "knowledge.ingest_status":
        args = {}
    elif chosen_name == "document.analyze":
        args = {"document_id": "latest"}
    else:
        args = {"query": query}

    return chosen_name, args


# ── response content ───────────────────────────────────────────────────────────

THINKING = (
    "Let me think about this carefully. "
    "I need to look at the available context and tools to give the best answer."
)

PLAIN_ANSWER = (
    "I've reviewed the conversation and I'm ready to help. "
    "Here's what I can tell you based on the information available."
)

AFTER_TOOL_ANSWER = (
    "Based on the results I retrieved, here is my response. "
    "I've incorporated the relevant information from the tool output into this answer. "
    "Let me know if you'd like me to search for anything else or provide more details."
)


# ── streaming helpers ──────────────────────────────────────────────────────────

def _stream_chunks(wfile, base: dict, texts: list[tuple[str, str]], flush):
    """Stream word-by-word chunks.  Each entry is (delta_key, text)."""
    for key, text in texts:
        for word in text.split():
            chunk = {**base, "choices": [{
                "index": 0, "finish_reason": None,
                "delta": {key: word + " "},
            }]}
            wfile.write(_sse(chunk))
            flush()
            time.sleep(0.015)


def _stream_text_response(wfile, model: str, messages: list[dict], flush):
    cid = _completion_id()
    base = {"id": cid, "object": "chat.completion.chunk", "model": model}

    # If the last role is 'tool', use the post-tool answer text
    last_role = messages[-1].get("role") if messages else ""
    answer = AFTER_TOOL_ANSWER if last_role == "tool" else PLAIN_ANSWER

    _stream_chunks(wfile, base, [
        ("reasoning_content", THINKING),
        ("content", answer),
    ], flush)

    done = {**base, "choices": [{
        "index": 0, "finish_reason": "stop", "delta": {},
    }], "usage": {"prompt_tokens": 25, "completion_tokens": 35, "total_tokens": 60}}
    wfile.write(_sse(done))
    flush()
    wfile.write(b"data: [DONE]\n\n")
    flush()


def _stream_tool_call_response(wfile, model: str, messages: list[dict],
                                tools: list[dict], flush):
    cid = _completion_id()
    tc_id = _tool_call_id()
    base = {"id": cid, "object": "chat.completion.chunk", "model": model}

    tool_name, args_dict = _pick_tool(messages, tools)
    args_str = json.dumps(args_dict)

    # 1. Reasoning
    _stream_chunks(wfile, base, [("reasoning_content", THINKING)], flush)

    # 2. First tool-call delta: id + name (arguments = "" per OpenAI spec)
    wfile.write(_sse({**base, "choices": [{
        "index": 0, "finish_reason": None,
        "delta": {"tool_calls": [{
            "index": 0,
            "id": tc_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": ""},
        }]},
    }]}))
    flush()
    time.sleep(0.02)

    # 3. Arguments streamed in pieces
    mid = len(args_str) // 2
    for piece in (args_str[:mid], args_str[mid:]):
        wfile.write(_sse({**base, "choices": [{
            "index": 0, "finish_reason": None,
            "delta": {"tool_calls": [{"index": 0, "function": {"arguments": piece}}]},
        }]}))
        flush()
        time.sleep(0.02)

    # 4. Finish
    wfile.write(_sse({**base, "choices": [{
        "index": 0, "finish_reason": "tool_calls", "delta": {},
    }], "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}}))
    flush()
    wfile.write(b"data: [DONE]\n\n")
    flush()


def _non_stream_response(model: str, messages: list[dict], tools: list[dict]) -> dict:
    cid = _completion_id()
    last_role = messages[-1].get("role") if messages else ""

    if tools and last_role != "tool":
        tool_name, args_dict = _pick_tool(messages, tools)
        return {
            "id": cid, "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant",
                "content": None,
                "reasoning_content": THINKING,
                "tool_calls": [{
                    "id": _tool_call_id(),
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args_dict)},
                }],
            }}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
        }

    answer = AFTER_TOOL_ANSWER if last_role == "tool" else PLAIN_ANSWER
    return {
        "id": cid, "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant",
            "content": answer,
            "reasoning_content": THINKING,
        }}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 35, "total_tokens": 60},
    }


# ── HTTP handler ───────────────────────────────────────────────────────────────

class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    embed_dim: int = 4096
    chat_model: str = "mock-chat"
    embed_model: str = "mock-embed"

    def log_message(self, fmt, *args):
        print(f"[mock] {self.address_string()} {fmt % args}", file=sys.stderr, flush=True)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._send_json({"object": "list", "data": [
                {"id": self.chat_model,  "object": "model", "owned_by": "mock"},
                {"id": self.embed_model, "object": "model", "owned_by": "mock"},
            ]})
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
        body     = self._read_body()
        model    = body.get("model", self.chat_model)
        stream   = body.get("stream", False)
        tools    = body.get("tools", [])
        messages = body.get("messages", [])

        # If last message is a tool result, always return a text response
        last_role = messages[-1].get("role") if messages else ""
        use_tool  = bool(tools) and last_role != "tool"

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def flush():
                try: self.wfile.flush()
                except BrokenPipeError: pass

            if use_tool:
                _stream_tool_call_response(self.wfile, model, messages, tools, flush)
            else:
                _stream_text_response(self.wfile, model, messages, flush)
        else:
            self._send_json(_non_stream_response(model, messages, tools))

    def _handle_embeddings(self):
        body   = self._read_body()
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        self._send_json({
            "object": "list",
            "model": body.get("model", self.embed_model),
            "data": [
                {"object": "embedding", "index": i,
                 "embedding": _fake_embedding(t, self.embed_dim)}
                for i, t in enumerate(inputs)
            ],
            "usage": {
                "prompt_tokens": sum(len(t.split()) for t in inputs),
                "total_tokens":  sum(len(t.split()) for t in inputs),
            },
        })


# ── entry point ────────────────────────────────────────────────────────────────

def make_handler(embed_dim, chat_model, embed_model):
    class H(MockHandler): pass
    H.embed_dim   = embed_dim
    H.chat_model  = chat_model
    H.embed_model = embed_model
    return H


def serve(port, handler_class, label):
    server = HTTPServer(("0.0.0.0", port), handler_class)
    print(f"[mock] {label} on http://0.0.0.0:{port}/v1", file=sys.stderr, flush=True)
    server.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port",        type=int, default=None)
    ap.add_argument("--chat-port",   type=int, default=9000)
    ap.add_argument("--embed-port",  type=int, default=9001)
    ap.add_argument("--embed-dim",   type=int,
                    default=int(os.environ.get("TEXT_EMBEDDING_DIM", "4096")))
    ap.add_argument("--chat-model",  default=os.environ.get("CHAT_MODEL",  "mock-chat"))
    ap.add_argument("--embed-model", default=os.environ.get("TEXT_EMBEDDING_MODEL", "mock-embed"))
    args = ap.parse_args()

    H = make_handler(args.embed_dim, args.chat_model, args.embed_model)

    if args.port is not None:
        serve(args.port, H, "chat+embeddings")
    else:
        threading.Thread(target=serve, args=(args.chat_port, H, "chat"), daemon=True).start()
        serve(args.embed_port, H, "embeddings")


if __name__ == "__main__":
    main()
