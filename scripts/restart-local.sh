#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

printf 'Stopping Podocracy Worker Portal...\n'
docker compose down

exec "$ROOT_DIR/scripts/start-local.sh"
