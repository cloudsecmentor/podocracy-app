#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/_launch-common.sh" ]]; then
  # shellcheck source=scripts/_launch-common.sh
  source "$SCRIPT_DIR/_launch-common.sh"
elif [[ -f "$SCRIPT_DIR/../scripts/_launch-common.sh" ]]; then
  # shellcheck source=scripts/_launch-common.sh
  source "$SCRIPT_DIR/../scripts/_launch-common.sh"
else
  printf 'Missing _launch-common.sh next to launch.sh (download it from the repo scripts/ folder).\n' >&2
  exit 1
fi

if [[ -f "$SCRIPT_DIR/docker-compose.images.yml" || -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  ROOT_DIR="$SCRIPT_DIR"
elif [[ -f "$SCRIPT_DIR/../docker-compose.images.yml" || -f "$SCRIPT_DIR/../docker-compose.yml" ]]; then
  ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

cd "$ROOT_DIR"

PORT="${PORTAL_HTTP_PORT:-8080}"
URL="http://localhost:${PORT}"

ensure_docker_running
COMPOSE_FILE="$(resolve_compose_file)"
compose_up "$COMPOSE_FILE"
wait_for_portal "$URL"
open_portal "$URL"
