#!/usr/bin/env python3
"""
Local document file server.

Stores files at:  DOCS_ROOT/<user_id>/<YYYY-MM-DD>/<category>/<filename>
  category is 'knowledge', 'chat', or custom.

HTTP API
--------
POST /upload
    Multipart form:
        file        — the file blob
        user_id     — uploader's UUID
        category    — folder name (default: "misc")
        date        — override date (default: today, YYYY-MM-DD)
    Returns JSON: {"ok": true, "path": "user_id/date/category/name", "size": N}

GET /files/<path>
    Serve file at DOCS_ROOT/<path>.  Returns 404 if missing.

GET /browse[?user=<user_id>][&date=<date>]
    Return JSON tree of stored files.

DELETE /files/<path>
    Remove file.  Returns JSON {"ok": true}.

Usage
-----
  python file_server/server.py                 # port 8888, DOCS_ROOT=./data/docs
  DOCS_ROOT=/mnt/storage PORT=9999 python file_server/server.py
"""

import cgi
import json
import mimetypes
import os
import sys
from datetime import date as _date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DOCS_ROOT = Path(os.environ.get("DOCS_ROOT", Path(__file__).parent.parent / "data" / "docs"))
PORT = int(os.environ.get("FILE_SERVER_PORT", 8888))


# ─── helpers ──────────────────────────────────────────────────────────────────

def _json(handler, code, body):
    data = json.dumps(body).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _safe_path(rel: str) -> Path | None:
    """Resolve relative path under DOCS_ROOT, reject path traversal."""
    try:
        full = (DOCS_ROOT / rel).resolve()
        if DOCS_ROOT.resolve() in full.parents or full == DOCS_ROOT.resolve():
            return full
    except Exception:
        pass
    return None


def _walk_tree(root: Path, base: Path):
    entries = []
    if not root.exists():
        return entries
    for item in sorted(root.iterdir()):
        rel = str(item.relative_to(base))
        if item.is_dir():
            entries.append({"type": "dir", "name": item.name, "path": rel,
                             "children": _walk_tree(item, base)})
        else:
            entries.append({"type": "file", "name": item.name, "path": rel,
                             "size": item.stat().st_size})
    return entries


# ─── handler ──────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[file-server] {self.address_string()} {fmt % args}", flush=True)

    # ── POST /upload ──────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/upload":
            _json(self, 404, {"ok": False, "error": "not found"})
            return

        ctype, pdict = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype != "multipart/form-data":
            _json(self, 400, {"ok": False, "error": "multipart required"})
            return

        pdict["boundary"] = pdict["boundary"].encode()
        length = int(self.headers.get("Content-Length", 0))
        form = cgi.parse_multipart(self.rfile, pdict)

        user_id  = (form.get("user_id", [b"anonymous"])[0] or b"anonymous")
        category = (form.get("category", [b"misc"])[0] or b"misc")
        day      = (form.get("date", [str(_date.today()).encode()])[0] or str(_date.today()).encode())
        files    = form.get("file", [])

        if isinstance(user_id, bytes):  user_id  = user_id.decode()
        if isinstance(category, bytes): category = category.decode()
        if isinstance(day, bytes):      day      = day.decode()

        if not files:
            _json(self, 400, {"ok": False, "error": "no file"})
            return

        raw_file = files[0]
        # filename from Content-Disposition isn't preserved by cgi.parse_multipart;
        # client should send it as a separate "filename" field.
        filename = (form.get("filename", [b"upload"])[0] or b"upload")
        if isinstance(filename, bytes):
            filename = filename.decode()

        rel_dir = Path(user_id) / day / category
        dest_dir = DOCS_ROOT / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / filename
        # Avoid overwrite
        stem, suffix = Path(filename).stem, Path(filename).suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        if isinstance(raw_file, bytes):
            dest.write_bytes(raw_file)
        else:
            dest.write_bytes(raw_file.encode())

        rel_path = str(dest.relative_to(DOCS_ROOT))
        _json(self, 200, {"ok": True, "path": rel_path, "size": dest.stat().st_size})

    # ── GET /files/<path>  or  GET /browse ────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/browse":
            qs = parse_qs(parsed.query)
            user = qs.get("user", [None])[0]
            day  = qs.get("date", [None])[0]
            root = DOCS_ROOT
            if user:
                root = root / user
            if day:
                root = root / day
            tree = _walk_tree(root, DOCS_ROOT)
            _json(self, 200, {"ok": True, "tree": tree})
            return

        if path.startswith("/files/"):
            rel = path[len("/files/"):]
            full = _safe_path(rel)
            if full is None or not full.exists() or not full.is_file():
                _json(self, 404, {"ok": False, "error": "not found"})
                return
            mime, _ = mimetypes.guess_type(str(full))
            data = full.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             f'inline; filename="{full.name}"')
            self.end_headers()
            self.wfile.write(data)
            return

        _json(self, 404, {"ok": False, "error": "not found"})

    # ── DELETE /files/<path> ──────────────────────────────────────────────────
    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/files/"):
            rel = parsed.path[len("/files/"):]
            full = _safe_path(rel)
            if full and full.exists() and full.is_file():
                full.unlink()
                _json(self, 200, {"ok": True})
                return
        _json(self, 404, {"ok": False, "error": "not found"})


# ─── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[file-server] Listening on :{PORT}  DOCS_ROOT={DOCS_ROOT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
