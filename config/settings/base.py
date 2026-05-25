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
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", BASE_DIR / "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# LiteLLM settings
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:8000/v1")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "local-placeholder")
QWEN_CHAT_MODEL = os.environ.get("QWEN_CHAT_MODEL", "Jackrong/Qwopus3.6-27B-v2-GGUF")
QWEN_VISION_MODEL = os.environ.get("QWEN_VISION_MODEL", "Jackrong/Qwopus3.6-27B-v2-GGUF")
QWEN_EMBEDDING_MODEL = os.environ.get("QWEN_EMBEDDING_MODEL", "nvidia/llama-embed-nemotron-8b")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "4096"))

# Chat settings
CHAT_CONTEXT_MAX_TOKENS = int(os.environ.get("CHAT_CONTEXT_MAX_TOKENS", "32000"))
CHAT_CONTEXT_MAX_TOKENS_LARGE = int(os.environ.get("CHAT_CONTEXT_MAX_TOKENS_LARGE", "128000"))
CHAT_RESPONSE_MAX_TOKENS = int(os.environ.get("CHAT_RESPONSE_MAX_TOKENS", "2048"))
CHAT_COMPACTION_THRESHOLD_TOKENS = int(os.environ.get("CHAT_COMPACTION_THRESHOLD_TOKENS", "14000"))
CHAT_STREAMING_ENABLED = os.environ.get("CHAT_STREAMING_ENABLED", "true").lower() in ("true", "1", "yes")

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
