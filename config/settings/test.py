from .base import *  # noqa: F403

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "chatbot_test"),  # noqa: F405
        "USER": os.environ.get("POSTGRES_USER", "chatbot"),  # noqa: F405
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "chatbot"),  # noqa: F405
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),  # noqa: F405
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),  # noqa: F405
        "TEST": {"NAME": os.environ.get("POSTGRES_TEST_DB", "chatbot_test")},  # noqa: F405
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
