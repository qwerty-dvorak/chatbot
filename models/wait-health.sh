#!/bin/bash
# Wait for HTTP health endpoints to return 200.
# Usage: bash wait-health.sh "name=url" "name2=url2" ...
set -euo pipefail

for pair in "$@"; do
  name="${pair%%=*}"
  url="${pair#*=}"
  max=1800
  elapsed=0
  echo -n "  $name healthy"
  while true; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      echo " ✓"
      break
    fi
    sleep 30
    elapsed=$((elapsed + 30))
    echo -n "."
    if [[ $elapsed -ge $max ]]; then
      echo " TIMEOUT (last HTTP $code)"
      exit 1
    fi
  done
done
