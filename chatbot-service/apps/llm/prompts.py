import os

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def _load(name: str) -> str:
    path = os.path.join(_PROMPTS_DIR, name)
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


SYSTEM_PROMPT = _load("system.txt")
MEMORY_CONTEXT_PROMPT = _load("memory_context.txt")
COMPACTION_CONTEXT_PROMPT = _load("compaction_context.txt")
DOCUMENT_SELECTION_CONTEXT_PROMPT = _load("document_selection_context.txt")
RAG_CONTEXT_PROMPT = _load("rag_context.txt")
RETRIEVAL_ROUTER_PROMPT = _load("retrieval_router.txt")
