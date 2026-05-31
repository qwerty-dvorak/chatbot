from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "chatbot"),  # noqa: F405
        "USER": os.environ.get("POSTGRES_USER", "chatbot"),  # noqa: F405
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "chatbot"),  # noqa: F405
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),  # noqa: F405
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),  # noqa: F405
    }
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
