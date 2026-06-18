#!/bin/bash
set -e

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
DB="${POSTGRES_DB:-chatbot}"
USER="${POSTGRES_USER:-chatbot}"
PASS="${POSTGRES_PASSWORD:-chatbot}"
PORT="${POSTGRES_PORT:-5433}"

if [ "$1" = 'postgres-server' ]; then
    echo "[postgres] Starting in Dedicated Database Mode..."

    mkdir -p "$PGDATA" /var/run/postgresql
    chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql

    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        echo "[postgres] Initializing cluster in $PGDATA"
        runuser -u postgres -- "$PGBIN/initdb" -D "$PGDATA" --encoding=UTF8 --locale=C.UTF-8

        printf "listen_addresses = '*'\nport = %s\n" "$PORT" >> "$PGDATA/postgresql.conf"
        printf "host all all 0.0.0.0/0 md5\n" >> "$PGDATA/pg_hba.conf"

        runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PORT" -w start

        runuser -u postgres -- psql -p "$PORT" -tc "SELECT 1 FROM pg_roles WHERE rolname='${USER}'" | grep -q 1 || \
            runuser -u postgres -- psql -p "$PORT" -c "CREATE USER \"${USER}\" WITH PASSWORD '${PASS}';"

        runuser -u postgres -- psql -p "$PORT" -tc "SELECT 1 FROM pg_database WHERE datname='${DB}'" | grep -q 1 || \
            runuser -u postgres -- psql -p "$PORT" -c "CREATE DATABASE \"${DB}\" OWNER \"${USER}\";"

        runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w stop
    fi

    echo "[postgres] Database ready. Running in foreground..."
    exec runuser -u postgres -- "$PGBIN/postgres" -D "$PGDATA" -p "$PORT"
fi

exec "$@"
