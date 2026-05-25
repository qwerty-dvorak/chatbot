from typing import Any

BUILTIN_TOOLS: dict[str, callable] = {}


def register_builtin(name: str):
    def decorator(func):
        BUILTIN_TOOLS[name] = func
        return func
    return decorator


@register_builtin("rag.search")
def rag_search(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "results": [],
        "message": "RAG search tool - not yet connected to knowledge base",
    }


@register_builtin("knowledge.ingest_status")
def ingest_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "unknown",
        "message": "Ingestion status tool - not yet connected",
    }


@register_builtin("memory.search")
def memory_search(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "memories": [],
        "message": "Memory search tool - not yet connected",
    }


@register_builtin("memory.save")
def memory_save(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "saved": True,
        "message": "Memory save tool - not yet connected",
    }


@register_builtin("chat.compact")
def chat_compact(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "compacted": True,
        "message": "Chat compaction tool - not yet connected",
    }


@register_builtin("document.analyze")
def document_analyze(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "analyzed": True,
        "message": "Document analysis tool - not yet connected",
    }
