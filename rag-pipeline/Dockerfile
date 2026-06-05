FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

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
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY pipeline/ ./pipeline/
COPY api.py ingest.py search.py ./

RUN mkdir -p /app/data /app/models

EXPOSE 8093

CMD ["uv", "run", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8093", "--workers", "2"]
