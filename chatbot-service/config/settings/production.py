from .base import *  # noqa: F403

DEBUG = False

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "chatbot"),
        "USER": os.environ.get("POSTGRES_USER", "chatbot"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "chatbot"),
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5433"),
    }
}
csrf_env = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
# This splits the string by commas into a real Python list
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_env.split(",") if origin.strip()]


# For intranet HTTP-only deployment, disable secure cookie flags
# (set HTTPS=true env var to re-enable when running behind TLS)
_https = os.environ.get("HTTPS", "false").lower() in ("true", "1", "yes")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if _https else None
SESSION_COOKIE_SECURE = _https
CSRF_COOKIE_SECURE = _https
