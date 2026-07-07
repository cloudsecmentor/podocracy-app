#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=scripts/_launch-common.sh
source "$ROOT_DIR/scripts/_launch-common.sh"

PORT="${PORTAL_HTTP_PORT:-8080}"
URL="http://localhost:${PORT}"

ensure_docker_running
compose_up "docker-compose.yml" "--build"
wait_for_portal "$URL"
open_portal "$URL"
