#!/usr/bin/env bash
set -euo pipefail

PORT="${PORTAL_HTTP_PORT:-8080}"
URL="http://localhost:${PORT}"

docker compose up --build -d

printf 'Waiting for %s/api/health' "$URL"
for _ in $(seq 1 60); do
  if curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
    printf '\nPortal is ready: %s\n' "$URL"
    if command -v open >/dev/null 2>&1; then
      open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$URL" >/dev/null 2>&1 || true
    else
      printf 'Open this URL in your browser: %s\n' "$URL"
    fi
    exit 0
  fi
  printf '.'
  sleep 1
done

printf '\nPortal did not become ready in time. Check logs with: docker compose logs\n' >&2
exit 1
