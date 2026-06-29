"""
Shared endpoint URL builders for all LLM API clients.

Each function takes a base URL (from settings) and returns the full endpoint URL.
Base URLs are normalised to strip /v1 suffixes so versioned paths can be rebuilt
consistently.
"""


def normalize_url(base_url: str) -> str:
    """Strip trailing /v1 (or any version prefix) and trailing slash."""
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url.rstrip("/")


def chat_completions_url(base_url: str) -> str:
    """OpenAI-compatible /v1/chat/completions endpoint."""
    return normalize_url(base_url) + "/v1/chat/completions"


def models_url(base_url: str) -> str:
    """OpenAI-compatible /v1/models endpoint (model list + LoRA discovery)."""
    return normalize_url(base_url) + "/v1/models"


def embeddings_url(base_url: str) -> str:
    """OpenAI-compatible /embeddings endpoint."""
    return normalize_url(base_url) + "/embeddings"


def score_url(base_url: str) -> str:
    """vLLM-native /score endpoint (pairwise relevance scoring)."""
    return base_url.rstrip("/") + "/score"


def rerank_url(base_url: str) -> str:
    """Cohere-compatible /v1/rerank endpoint."""
    return normalize_url(base_url) + "/v1/rerank"


def tokenize_url(base_url: str) -> str:
    """vLLM-native /tokenize endpoint."""
    return normalize_url(base_url) + "/tokenize"
