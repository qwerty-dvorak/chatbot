#!/usr/bin/env python3
"""
Mock vLLM / OpenAI-compatible server for chatbot-service local development.

When GEMINI_API_KEY is set, chat completions are proxied to the real Gemini API
so that RAG context and tool calls work end-to-end without a local LLM.
Otherwise falls back to canned responses for offline testing.

Supports:
  - GET  /health, /health/live, /health/ready
  - GET  /v1/models
  - POST /v1/chat/completions (streaming and non-streaming)
      - Proxies to Gemini API when GEMINI_API_KEY is set
      - Falls back to keyword-based tool emulation when not set
      - Converters between OpenAI and Gemini message/tool formats
  - POST /v1/embeddings
  - POST /score (reranker compatibility)
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-exp")


# ---------------------------------------------------------------------------
# Gemini API proxy helpers
# ---------------------------------------------------------------------------

def _openai_to_gemini_messages(messages: list[dict]) -> tuple[dict | None, list[dict]]:
    system = None
    contents = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            system = {"parts": [{"text": content}]}
        elif role == "user":
            parts = [{"text": content or ""}]
            contents.append({"role": "user", "parts": parts})
        elif role == "assistant":
            parts = []
            if content:
                parts.append({"text": content})
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                parts.append({
                    "functionCall": {"name": func.get("name", ""), "args": args},
                })
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            tc_id = msg.get("tool_call_id", "")
            name = msg.get("name", "unknown")
            # Try to find the name from preceding assistant message with tool_calls
            for prev in reversed(messages[:contents.__len__() + 1]):
                if isinstance(prev, dict) and prev.get("role") == "assistant":
                    for tc in prev.get("tool_calls", []):
                        if tc.get("id") == tc_id:
                            name = tc.get("function", {}).get("name", name)
                            break
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": name,
                        "response": {"content": content or ""},
                    }
                }],
            })
    return system, contents


def _openai_to_gemini_tools(tools: list[dict]) -> list[dict]:
    func_decls = []
    for t in tools:
        func = t.get("function", {})
        decl = {"name": func.get("name", "")}
        if func.get("description"):
            decl["description"] = func["description"]
        if func.get("parameters"):
            decl["parameters"] = func["parameters"]
        func_decls.append(decl)
    return [{"functionDeclarations": func_decls}] if func_decls else []


def _gemini_to_openai_response(gemini_resp: dict, model: str) -> dict:
    cid = _completion_id()
    candidates = gemini_resp.get("candidates", [])
    if not candidates:
        return {"id": cid, "object": "chat.completion", "model": model,
                "choices": [], "usage": {}}

    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])
    finish_reason = candidate.get("finishReason", "").upper()

    reason_map = {
        "STOP": "stop", "MAX_TOKENS": "length",
        "SAFETY": "content_filter", "RECITATION": "content_filter",
        "PROHIBITED": "content_filter", "OTHER": "stop",
        "BLOCKLIST": "content_filter", "FUNCTION_CALL": "tool_calls",
    }
    openai_reason = reason_map.get(finish_reason, "stop")

    text_parts = []
    tool_calls = []
    for i, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        if "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {})),
                },
            })

    if candidate.get("finishReason") == "SAFETY" and not text_parts:
        safety_ratings = candidate.get("safetyRatings", [])
        blocked = [r["category"] for r in safety_ratings if r.get("probability", "").upper() not in ("NEGLIGIBLE",)]
        text_parts.append(f"[Content blocked by safety: {', '.join(blocked)}]")

    message = {"role": "assistant"}
    if text_parts:
        message["content"] = "".join(text_parts)
    else:
        message["content"] = None
    if tool_calls:
        message["tool_calls"] = tool_calls
        openai_reason = "tool_calls"

    usage = gemini_resp.get("usageMetadata", {})
    return {
        "id": cid, "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": openai_reason}],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


def _call_gemini(messages: list[dict], tools: list[dict]) -> dict:
    system, contents = _openai_to_gemini_messages(messages)
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    body = {"contents": contents}
    if system:
        body["system_instruction"] = system
    gemini_tools = _openai_to_gemini_tools(tools)
    if gemini_tools:
        body["tools"] = gemini_tools

    import urllib.error
    import urllib.request
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        resp_data = json.loads(resp.read().decode())
        return _gemini_to_openai_response(resp_data, GEMINI_MODEL)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode()
        return {
            "id": _completion_id(), "object": "chat.completion", "model": GEMINI_MODEL,
            "choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": f"Gemini API error ({exc.code}): {error_body[:500]}",
            }, "finish_reason": "stop"}],
            "usage": {},
        }
    except Exception as exc:
        return {
            "id": _completion_id(), "object": "chat.completion", "model": GEMINI_MODEL,
            "choices": [{"index": 0, "message": {
                "role": "assistant",
                "content": f"Gemini error: {exc}",
            }, "finish_reason": "stop"}],
            "usage": {},
        }


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Canned keyword-based tool selection (fallback when no GEMINI_API_KEY)
# ---------------------------------------------------------------------------

_TOOL_KEYWORDS: list[tuple[list[str], str]] = [
    (["remember", "save memory", "note that", "memorize", "store memory",
      "i like", "i prefer", "i use", "i want you to know", "keep in mind"],     "memory.save"),
    (["search my memory", "search memory", "recall", "what do you remember",
      "what do you know about me", "what did i tell"],                           "memory.search"),
    (["knowledge base", "search document", "find document", "rag",
      "search knowledge", "look up"],                                            "rag.search"),
    (["compact", "summarize chat", "compress"],                                  "chat.compact"),
    (["ingest status", "ingestion"],                                             "knowledge.ingest_status"),
    (["analyze document", "analyse document", "document analysis"],              "document.analyze"),
]

def _extract_query(text: str) -> str:
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
    available_names = {t.get("function", {}).get("name", "") for t in tools}
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


PLAIN_ANSWER = (
    "I've reviewed the conversation and I'm ready to help. "
    "Here's what I can tell you based on the information available."
)
AFTER_TOOL_ANSWER = (
    "Based on the results I retrieved, here is my response. "
    "I've incorporated the relevant information from the tool output into this answer."
)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    embed_dim: int = 768
    chat_model: str = "mock-chat"
    embed_model: str = "mock-text-embed"

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

    # ── GET ────────────────────────────────────────────────────────────────────

    def do_GET(self):
        path = self.path.rstrip("/")
        if path in ("/health", "/health/live", "/health/ready"):
            self._send_json({"status": "ok", "service": "mock-chat",
                             "gemini": bool(GEMINI_API_KEY)})
        elif path == "/v1/models":
            models = [
                {"id": self.chat_model,  "object": "model", "owned_by": "mock"},
                {"id": self.embed_model, "object": "model", "owned_by": "mock"},
            ]
            if GEMINI_API_KEY:
                models.insert(0, {"id": GEMINI_MODEL, "object": "model", "owned_by": "google"})
            self._send_json({"object": "list", "data": models})
        else:
            self._send_json({"error": "not found"}, 404)

    # ── POST ───────────────────────────────────────────────────────────────────

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/chat/completions":
            self._handle_chat()
        elif path == "/v1/embeddings":
            self._handle_embeddings()
        elif path == "/score":
            self._handle_score()
        else:
            self._send_json({"error": "not found"}, 404)

    # ── Chat completions ────────────────────────────────────────────────────────

    def _handle_chat(self):
        body     = self._read_body()
        model    = body.get("model", self.chat_model)
        stream   = body.get("stream", False)
        tools    = body.get("tools", [])
        messages = body.get("messages", [])
        last_role = messages[-1].get("role") if messages else ""

        if GEMINI_API_KEY:
            # Proxy to Gemini
            gemini_resp = _call_gemini(messages, tools)
            if stream:
                self._stream_gemini_response(gemini_resp, model)
            else:
                self._send_json(gemini_resp)
        else:
            # Fallback: keyword-based tool emulation
            use_tool = bool(tools) and last_role != "tool"
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                def flush():
                    try: self.wfile.flush()
                    except BrokenPipeError: pass
                if use_tool:
                    self._stream_tool_call(self.wfile, model, messages, tools, flush)
                else:
                    self._stream_canned_text(self.wfile, model, messages, flush)
            else:
                self._send_json(self._canned_response(model, messages, tools))

    def _stream_gemini_response(self, gemini_resp: dict, model: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        def flush():
            try: self.wfile.flush()
            except BrokenPipeError: pass

        choices = gemini_resp.get("choices", [])
        if not choices:
            self.wfile.write(b"data: [DONE]\n\n")
            flush()
            return

        choice = choices[0]
        message = choice.get("message", {})
        finish = choice.get("finish_reason", "stop")
        cid = gemini_resp.get("id", _completion_id())
        base = {"id": cid, "object": "chat.completion.chunk", "model": model}

        tool_calls = message.get("tool_calls", [])
        content = message.get("content") or ""

        if tool_calls:
            # Stream tool call deltas
            for tc in tool_calls:
                tc_id = tc["id"]
                func = tc.get("function", {})
                args_str = func.get("arguments", "{}")
                # First delta: id + name
                self.wfile.write(_sse({**base, "choices": [{
                    "index": 0, "finish_reason": None,
                    "delta": {"tool_calls": [{
                        "index": 0, "id": tc_id, "type": "function",
                        "function": {"name": func.get("name", ""), "arguments": ""},
                    }]},
                }]}))
                flush()
                time.sleep(0.02)
                # Argument chunks
                mid = len(args_str) // 2
                for piece in (args_str[:mid], args_str[mid:]):
                    self.wfile.write(_sse({**base, "choices": [{
                        "index": 0, "finish_reason": None,
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": piece}}]},
                    }]}))
                    flush()
                    time.sleep(0.02)
            done = {**base, "choices": [{
                "index": 0, "finish_reason": "tool_calls", "delta": {},
            }], "usage": gemini_resp.get("usage", {})}
        elif content:
            # Stream content word-by-word
            for word in content.split():
                self.wfile.write(_sse({**base, "choices": [{
                    "index": 0, "finish_reason": None,
                    "delta": {"content": word + " "},
                }]}))
                flush()
                time.sleep(0.015)
            done = {**base, "choices": [{
                "index": 0, "finish_reason": "stop", "delta": {},
            }], "usage": gemini_resp.get("usage", {})}
        else:
            done = {**base, "choices": [{
                "index": 0, "finish_reason": finish, "delta": {},
            }], "usage": gemini_resp.get("usage", {})}

        self.wfile.write(_sse(done))
        flush()
        self.wfile.write(b"data: [DONE]\n\n")
        flush()

    def _stream_canned_text(self, wfile, model, messages, flush):
        cid = _completion_id()
        base = {"id": cid, "object": "chat.completion.chunk", "model": model}
        last_role = messages[-1].get("role") if messages else ""
        answer = AFTER_TOOL_ANSWER if last_role == "tool" else PLAIN_ANSWER
        for word in answer.split():
            chunk = {**base, "choices": [{
                "index": 0, "finish_reason": None,
                "delta": {"content": word + " "},
            }]}
            wfile.write(_sse(chunk))
            flush()
            time.sleep(0.015)
        done = {**base, "choices": [{
            "index": 0, "finish_reason": "stop", "delta": {},
        }], "usage": {"prompt_tokens": 25, "completion_tokens": 35, "total_tokens": 60}}
        wfile.write(_sse(done))
        flush()
        wfile.write(b"data: [DONE]\n\n")
        flush()

    def _stream_tool_call(self, wfile, model, messages, tools, flush):
        cid = _completion_id()
        tc_id = _tool_call_id()
        base = {"id": cid, "object": "chat.completion.chunk", "model": model}
        tool_name, args_dict = _pick_tool(messages, tools)
        args_str = json.dumps(args_dict)
        wfile.write(_sse({**base, "choices": [{
            "index": 0, "finish_reason": None,
            "delta": {"tool_calls": [{
                "index": 0, "id": tc_id, "type": "function",
                "function": {"name": tool_name, "arguments": ""},
            }]},
        }]}))
        flush()
        time.sleep(0.02)
        mid = len(args_str) // 2
        for piece in (args_str[:mid], args_str[mid:]):
            wfile.write(_sse({**base, "choices": [{
                "index": 0, "finish_reason": None,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": piece}}]},
            }]}))
            flush()
            time.sleep(0.02)
        wfile.write(_sse({**base, "choices": [{
            "index": 0, "finish_reason": "tool_calls", "delta": {},
        }], "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32}}))
        flush()
        wfile.write(b"data: [DONE]\n\n")
        flush()

    def _canned_response(self, model, messages, tools) -> dict:
        cid = _completion_id()
        last_role = messages[-1].get("role") if messages else ""
        if tools and last_role != "tool":
            tool_name, args_dict = _pick_tool(messages, tools)
            return {
                "id": cid, "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": _tool_call_id(), "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps(args_dict)},
                    }],
                }}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
            }
        answer = AFTER_TOOL_ANSWER if last_role == "tool" else PLAIN_ANSWER
        return {
            "id": cid, "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": answer,
            }}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 35, "total_tokens": 60},
        }

    # ── Embeddings ──────────────────────────────────────────────────────────────

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

    # ── Reranker score ──────────────────────────────────────────────────────────

    def _handle_score(self):
        body = self._read_body()
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
            self._send_json({"error": "query and document counts must match"}, 400)
            return
        data = []
        total_tokens = 0
        for index, (query, document) in enumerate(zip(queries, documents)):
            seed = hash(f"{query}\0{document}") & 0x7FFFFFFF
            rng = random.Random(seed)
            data.append({
                "index": index,
                "object": "score",
                "score": round(rng.random(), 4),
            })
            total_tokens += len(query.split()) + len(document.split())
        self._send_json({
            "id": "score-" + uuid.uuid4().hex[:20],
            "object": "list",
            "data": data,
            "model": body.get("model", "mock-reranker"),
            "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
        })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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
    ap = argparse.ArgumentParser(description="Mock LiteLLM server for chatbot-service")
    ap.add_argument("--port",        type=int, default=None,
                    help="Single port for all endpoints")
    ap.add_argument("--chat-port",   type=int, default=9000)
    ap.add_argument("--embed-port",  type=int, default=9001)
    ap.add_argument("--rerank-port", type=int, default=9003)
    ap.add_argument("--embed-dim",   type=int,
                    default=int(os.environ.get("TEXT_EMBEDDING_DIM", "768")))
    ap.add_argument("--chat-model",  default=os.environ.get("CHAT_MODEL",  "mock-chat"))
    ap.add_argument("--embed-model", default=os.environ.get("TEXT_EMBEDDING_MODEL", "mock-text-embed"))
    args = ap.parse_args()

    H = make_handler(args.embed_dim, args.chat_model, args.embed_model)

    if GEMINI_API_KEY:
        print("[mock] Gemini API proxy ENABLED — chat requests will use real Gemini", file=sys.stderr, flush=True)
        print(f"[mock] Gemini model: {GEMINI_MODEL}", file=sys.stderr, flush=True)

    if args.port is not None:
        serve(args.port, H, "chat+embeddings+rerank")
    else:
        if args.chat_port:
            threading.Thread(target=serve, args=(args.chat_port, H, "chat"), daemon=True).start()
        if args.embed_port:
            threading.Thread(target=serve, args=(args.embed_port, H, "embeddings"), daemon=True).start()
        if args.rerank_port:
            threading.Thread(target=serve, args=(args.rerank_port, H, "reranker"), daemon=True).start()
        threading.Event().wait()


if __name__ == "__main__":
    main()
