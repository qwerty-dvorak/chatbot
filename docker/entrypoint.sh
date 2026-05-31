#!/bin/bash
set -e

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
DB="${POSTGRES_DB:-chatbot}"
USER="${POSTGRES_USER:-chatbot}"
PASS="${POSTGRES_PASSWORD:-chatbot}"
HOST="${POSTGRES_HOST:-localhost}"

# Only manage embedded PostgreSQL in standalone mode (no external POSTGRES_HOST).
# When running via docker-compose, POSTGRES_HOST=postgres (the service name), so
# this block is skipped and the app connects to the external postgres container.
if [ "$HOST" = "localhost" ] || [ "$HOST" = "127.0.0.1" ]; then
    mkdir -p "$PGDATA" /var/run/postgresql
    chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql

    if [ ! -f "$PGDATA/PG_VERSION" ]; then
        echo "[postgres] Initializing cluster in $PGDATA"
        runuser -u postgres -- "$PGBIN/initdb" -D "$PGDATA" --encoding=UTF8 --locale=C.UTF-8
        printf "listen_addresses = 'localhost'\n" >> "$PGDATA/postgresql.conf"
        printf "host all all 127.0.0.1/32 md5\n" >> "$PGDATA/pg_hba.conf"
        printf "host all all ::1/128 md5\n" >> "$PGDATA/pg_hba.conf"
    fi

    runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w start

    for i in $(seq 1 30); do
        runuser -u postgres -- pg_isready -q 2>/dev/null && break
        sleep 1
    done

    runuser -u postgres -- psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${USER}'" \
        | grep -q 1 2>/dev/null || \
        runuser -u postgres -- psql -c "CREATE USER \"${USER}\" WITH PASSWORD '${PASS}';"

    runuser -u postgres -- psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}'" \
        | grep -q 1 2>/dev/null || \
        runuser -u postgres -- psql -c "CREATE DATABASE \"${DB}\" OWNER \"${USER}\";"

    echo "[postgres] Ready"
fi

exec "$@"
