#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

printf 'Stopping Podocracy Worker Portal...\n'
docker compose down
printf 'Portal stopped. Project data remains in data/projects/\n'
