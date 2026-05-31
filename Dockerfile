FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# BARC-specific: Clear out default public repositories
# RUN rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list

# BARC-specific: Overwrite sources.list with local BARC repository
# RUN echo "deb http://osrepo.barc.gov.in/ubuntu/ noble main restricted universe multiverse\n\
# deb http://osrepo.barc.gov.in/ubuntu/ noble-updates main restricted universe multiverse\n\
# deb http://osrepo.barc.gov.in/ubuntu/ noble-security main restricted universe multiverse" > /etc/apt/sources.list

ENV DEBIAN_FRONTEND=noninteractive

ENV POSTGRES_DB=chatbot \
    POSTGRES_USER=chatbot \
    POSTGRES_PASSWORD=chatbot \
    PGDATA=/var/lib/postgresql/data \
    PGBIN=/usr/lib/postgresql/16/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
# BARC-specific: UV index URL and insecure host
#    UV_INDEX_URL=http://osrepo.barc.gov.in/python-pypi/simple \
#    UV_INSECURE_HOST=osrepo.barc.gov.in

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    gcc \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    libpq-dev \
    postgresql-16 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages uv

# Drop the auto-created cluster; the entrypoint manages PGDATA at runtime
RUN pg_dropcluster 16 main --stop 2>/dev/null || true && \
    mkdir -p /var/lib/postgresql/data && \
    chown -R postgres:postgres /var/lib/postgresql

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY . /app/

RUN uv run python manage.py collectstatic --noinput --settings=config.settings.production

COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# Standalone default: run migrations then start gunicorn.
# docker-compose overrides CMD per service.
CMD ["sh", "-c", "uv run python manage.py migrate --settings=config.settings.production && \
                  uv run gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4"]
