"""System prompt templates loaded from disk."""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load(name: str) -> str:
    path = _PROMPTS_DIR / name
    try:
        with path.open() as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


SYSTEM_PROMPT = _load("system.txt")
MEMORY_CONTEXT_PROMPT = _load("memory_context.txt")
COMPACTION_CONTEXT_PROMPT = _load("compaction_context.txt")
DOCUMENT_SELECTION_CONTEXT_PROMPT = _load("document_selection_context.txt")
RAG_CONTEXT_PROMPT = _load("rag_context.txt")
RETRIEVAL_ROUTER_PROMPT = _load("retrieval_router.txt")
