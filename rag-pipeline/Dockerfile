# BEGIN BARC BASE IMAGE
FROM ubuntu:24.04
# END BARC BASE IMAGE

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# BEGIN BARC APT SOURCES
# Public Ubuntu sources from the base image remain enabled.
# END BARC APT SOURCES

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
    git \
    && rm -rf /var/lib/apt/lists/*

# BEGIN BARC UV INSTALL
RUN pip install --no-cache-dir --break-system-packages uv
# END BARC UV INSTALL

WORKDIR /app

COPY pyproject.toml uv.lock ./
# BEGIN BARC UV SYNC
RUN uv sync --frozen --no-dev
# END BARC UV SYNC

COPY pipeline/ ./pipeline/
COPY api.py ingest.py search.py ./

RUN mkdir -p /app/data /app/models

EXPOSE 8093

CMD ["uv", "run", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8093", "--workers", "1"]
