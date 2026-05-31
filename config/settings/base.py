import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")

DEBUG = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.chat",
    "apps.memory",
    "apps.knowledge",
    "apps.ingestion",
    "apps.llm",
    "apps.tools",
    "apps.compaction",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.chat.context_processors.sidebar",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "media/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))

# Local document storage root — organised as docs/<user_id>/<date>/<category>/<file>
DOCS_ROOT = os.environ.get("DOCS_ROOT", str(BASE_DIR / "data" / "docs"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# Chat model endpoint (vLLM or any OpenAI-compatible server)
CHAT_BASE_URL = os.environ.get("CHAT_BASE_URL", "http://localhost:9000/v1")
CHAT_API_KEY = os.environ.get("CHAT_API_KEY", "local-placeholder")

# Embedding model endpoint (separate vLLM instance or same server)
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", "http://localhost:9001/v1")
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "local-placeholder")

# Reranker endpoint (can share the embedding server or be a separate instance)
RERANKER_BASE_URL = os.environ.get("RERANKER_BASE_URL", "http://localhost:9001/v1")
RERANKER_API_KEY = os.environ.get("RERANKER_API_KEY", "local-placeholder")

# Chat model: Gemma 4 26B A4B IT (MoE, 256K context)
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gemma-4-26b-a4b-it")
VISION_MODEL = os.environ.get("VISION_MODEL", "gemma-4-26b-a4b-it")

# Text embedding: nvidia/llama-embed-nemotron-8b (dim: 4096)
TEXT_EMBEDDING_MODEL = os.environ.get(
    "TEXT_EMBEDDING_MODEL", "nvidia/llama-embed-nemotron-8b"
)
TEXT_EMBEDDING_DIM = int(os.environ.get("TEXT_EMBEDDING_DIM", "4096"))

# Multimodal embedding: nvidia/nemotron-colembed-vl-8b-v2 (dim: 4096)
MULTIMODAL_EMBEDDING_MODEL = os.environ.get(
    "MULTIMODAL_EMBEDDING_MODEL", "nvidia/nemotron-colembed-vl-8b-v2"
)
MULTIMODAL_EMBEDDING_DIM = int(os.environ.get("MULTIMODAL_EMBEDDING_DIM", "4096"))

# Reranker: Qwen3-VL-Reranker-8B (score-only, no dim needed)
RERANKER_MODEL = os.environ.get(
    "RERANKER_MODEL", "Qwen/Qwen3-VL-Reranker-8B"
)

# Milvus settings
MILVUS_HOST = os.environ.get("MILVUS_HOST", "localhost")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
MILVUS_ALIAS = os.environ.get("MILVUS_ALIAS", "default")
MILVUS_COLLECTION_CHUNKS = os.environ.get(
    "MILVUS_COLLECTION_CHUNKS", "document_chunks"
)
MILVUS_COLLECTION_MEMORIES = os.environ.get(
    "MILVUS_COLLECTION_MEMORIES", "user_memories"
)

# Chat settings
CHAT_CONTEXT_MAX_TOKENS = int(os.environ.get("CHAT_CONTEXT_MAX_TOKENS", "32000"))
CHAT_CONTEXT_MAX_TOKENS_LARGE = int(os.environ.get("CHAT_CONTEXT_MAX_TOKENS_LARGE", "256000"))
CHAT_RESPONSE_MAX_TOKENS = int(os.environ.get("CHAT_RESPONSE_MAX_TOKENS", "4096"))
CHAT_COMPACTION_THRESHOLD_TOKENS = int(os.environ.get("CHAT_COMPACTION_THRESHOLD_TOKENS", "14000"))
CHAT_STREAMING_ENABLED = os.environ.get("CHAT_STREAMING_ENABLED", "true").lower() in ("true", "1", "yes")

# RAG feature flag
RAG_ENABLED = os.environ.get("RAG_ENABLED", "true").lower() in ("true", "1", "yes")

# RAG settings
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "8"))
RAG_MIN_SIMILARITY = float(os.environ.get("RAG_MIN_SIMILARITY", "0.45"))
RAG_CHUNK_TARGET_TOKENS = int(os.environ.get("RAG_CHUNK_TARGET_TOKENS", "700"))
RAG_CHUNK_OVERLAP_TOKENS = int(os.environ.get("RAG_CHUNK_OVERLAP_TOKENS", "120"))

# Upload settings
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
INGESTION_SYNC = os.environ.get("INGESTION_SYNC", "false").lower() in ("true", "1", "yes")
KNOWLEDGE_DEFAULT_VISIBILITY = os.environ.get("KNOWLEDGE_DEFAULT_VISIBILITY", "private")
MEMORY_AUTO_SAVE_DEFAULT = os.environ.get("MEMORY_AUTO_SAVE_DEFAULT", "true").lower() in ("true", "1", "yes")

# Tool settings
TOOL_CALLS_ENABLED = os.environ.get("TOOL_CALLS_ENABLED", "true").lower() in ("true", "1", "yes")
TOOL_CALL_TIMEOUT_SECONDS = int(os.environ.get("TOOL_CALL_TIMEOUT_SECONDS", "60"))
TOOL_RESULT_MAX_TOKENS = int(os.environ.get("TOOL_RESULT_MAX_TOKENS", "4000"))
