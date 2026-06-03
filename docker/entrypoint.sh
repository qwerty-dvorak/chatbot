#!/bin/bash
set -e

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
DB="${POSTGRES_DB:-chatbot}"
USER="${POSTGRES_USER:-chatbot}"
PASS="${POSTGRES_PASSWORD:-chatbot}"

# ---------------------------------------------------------
# ROLE 1: DEDICATED DATABASE CONTAINER (Triggered by Compose)
# ---------------------------------------------------------
if [ "$1" = 'postgres-server' ]; then
    echo "[postgres] Starting in Dedicated Database Mode..."
    
    mkdir -p "$PGDATA" /var/run/postgresql
    chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql

    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        echo "[postgres] Initializing cluster in $PGDATA"
        runuser -u postgres -- "$PGBIN/initdb" -D "$PGDATA" --encoding=UTF8 --locale=C.UTF-8
        
        # CRITICAL FIX: Allow external connections from other containers
        printf "listen_addresses = '*'\n" >> "$PGDATA/postgresql.conf"
        printf "host all all 0.0.0.0/0 md5\n" >> "$PGDATA/pg_hba.conf"
        
        # Temporarily start to create user/db
        runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w start
        
        runuser -u postgres -- psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${USER}'" | grep -q 1 || \
            runuser -u postgres -- psql -c "CREATE USER \"${USER}\" WITH PASSWORD '${PASS}';"
            
        runuser -u postgres -- psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}'" | grep -q 1 || \
            runuser -u postgres -- psql -c "CREATE DATABASE \"${DB}\" OWNER \"${USER}\";"
            
        runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w stop
    fi
    
    echo "[postgres] Database ready. Running in foreground..."
    # CRITICAL FIX: Run postgres directly in the foreground so the container stays alive
    exec runuser -u postgres -- "$PGBIN/postgres" -D "$PGDATA"
fi

# ---------------------------------------------------------
# ROLE 2: STANDALONE MONOLITH (For local testing without Compose)
# ---------------------------------------------------------
HOST="${POSTGRES_HOST:-localhost}"
if [ "$HOST" = "localhost" ] || [ "$HOST" = "127.0.0.1" ]; then
    echo "[postgres] Starting in Standalone Monolith Mode (DB + App)..."
    mkdir -p "$PGDATA" /var/run/postgresql
    chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql

    if [ ! -f "$PGDATA/PG_VERSION" ]; then
        runuser -u postgres -- "$PGBIN/initdb" -D "$PGDATA" --encoding=UTF8 --locale=C.UTF-8
        printf "listen_addresses = 'localhost'\n" >> "$PGDATA/postgresql.conf"
        printf "host all all 127.0.0.1/32 md5\n" >> "$PGDATA/pg_hba.conf"
    fi

    runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w start

    for i in $(seq 1 30); do
        runuser -u postgres -- pg_isready -q 2>/dev/null && break
        sleep 1
    done

    runuser -u postgres -- psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${USER}'" | grep -q 1 || \
        runuser -u postgres -- psql -c "CREATE USER \"${USER}\" WITH PASSWORD '${PASS}';"

    runuser -u postgres -- psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}'" | grep -q 1 || \
        runuser -u postgres -- psql -c "CREATE DATABASE \"${DB}\" OWNER \"${USER}\";"
fi

# ---------------------------------------------------------
# ROLE 3: APPLICATION SERVER (Web / Worker in Compose)
# ---------------------------------------------------------
# If the command isn't 'postgres-server', it will execute the Django commands here.
exec "$@"
