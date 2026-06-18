# BEGIN BARC BASE IMAGE
FROM ubuntu:24.04
# END BARC BASE IMAGE

ARG DEBIAN_FRONTEND=noninteractive

# BEGIN BARC APT SOURCES
# Public Ubuntu sources from the base image remain enabled.
# END BARC APT SOURCES

ENV DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DJANGO_SETTINGS_MODULE=config.settings.test

# BEGIN BARC UV ENV
# Public PyPI is used.
# END BARC UV ENV

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    gcc \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# BEGIN BARC UV INSTALL
RUN pip install --no-cache-dir --break-system-packages uv
# END BARC UV INSTALL

WORKDIR /app

COPY chatbot-service/pyproject.toml chatbot-service/uv.lock chatbot-service/README.md /app/
# BEGIN BARC UV SYNC
RUN uv sync --frozen --no-dev
# END BARC UV SYNC

COPY chatbot-service/ /app/

ENTRYPOINT ["uv", "run", "python", "manage.py", "test"]
CMD ["--settings=config.settings.test"]
