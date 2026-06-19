#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    from dotenv import load_dotenv

    load_dotenv()
    mode = os.environ.get("MODE", "local").lower()
    model_env = Path(__file__).resolve().parent.parent / "models" / f".env.{mode}"
    if model_env.exists():
        load_dotenv(str(model_env), override=False)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
