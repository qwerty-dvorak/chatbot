#!/bin/bash
# WARNING: Destroys ALL PostgreSQL data and recreates the volume.
# Useful for development/reset scenarios.
#
# Usage: bash clear.sh
set -euo pipefail

echo "WARNING: This will delete ALL PostgreSQL data!"
read -rp "Type 'yes' to confirm: " confirm
[[ "$confirm" != "yes" ]] && { echo "Aborted."; exit 1; }

docker rm -f chatbot-postgres 2>/dev/null || true
docker volume rm postgres_data 2>/dev/null || true
echo "PostgreSQL data cleared. Run 'bash start.sh' to recreate."
