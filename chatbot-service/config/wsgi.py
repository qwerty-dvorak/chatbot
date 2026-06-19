import os
from pathlib import Path

from dotenv import load_dotenv
from django.core.wsgi import get_wsgi_application

load_dotenv()
mode = os.environ.get("MODE", "local").lower()
model_env = Path(__file__).resolve().parent.parent.parent / "models" / f".env.{mode}"
if model_env.exists():
    load_dotenv(str(model_env), override=False)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
