#!/bin/bash
# Stop the PostgreSQL container.
#
# Usage: bash stop.sh
set -euo pipefail

echo "Stopping chatbot-postgres..."
docker stop chatbot-postgres 2>/dev/null && echo "Stopped." || echo "Container not running."
