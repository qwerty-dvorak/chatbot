"""
Gemma 4 26B A4B IT OpenAI-compatible API server via llama.cpp GGUF.

Expects GEMMA4_MODEL_PATH env var pointing to a GGUF file
or mount models/gemma4-26b-a4b-it-q4_k_m.gguf at /models/gemma4.gguf
"""

import json
import os
import subprocess
import sys

MODEL_PATH = os.environ.get("GEMMA4_MODEL_PATH", "/models/gemma4.gguf")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8001"))
N_GPU_LAYERS = int(os.environ.get("N_GPU_LAYERS", "-1"))
CTX_SIZE = int(os.environ.get("CTX_SIZE", "32768"))

if not os.path.exists(MODEL_PATH):
    print(f"Model not found at {MODEL_PATH}", file=sys.stderr)
    sys.exit(1)

cmd = [
    "llama-server",
    "-m", MODEL_PATH,
    "--host", HOST,
    "--port", str(PORT),
    "--ctx-size", str(CTX_SIZE),
    "--ngl", str(N_GPU_LAYERS),
    "--parallel", "1",
]

if __name__ == "__main__":
    os.execvp(cmd[0], cmd)
