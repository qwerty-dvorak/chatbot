# BEGIN BARC BASE IMAGE
FROM ubuntu:24.04
# END BARC BASE IMAGE

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# BEGIN BARC APT SOURCES
# Public Ubuntu sources from the base image remain enabled.
# END BARC APT SOURCES

# BEGIN BARC UV ENV
# Public PyPI is used.
# END BARC UV ENV

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# BEGIN BARC UV INSTALL
RUN pip install --no-cache-dir --break-system-packages uv
# END BARC UV INSTALL

WORKDIR /app

COPY pyproject.toml .
# BEGIN BARC UV SYNC
RUN uv sync --frozen --no-dev
# END BARC UV SYNC

COPY serve.py .

EXPOSE 8004

CMD ["uv", "run", "python", "serve.py"]
