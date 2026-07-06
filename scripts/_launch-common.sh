#!/usr/bin/env bash

ensure_docker_running() {
  if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker is not installed. Install Docker Desktop or Docker Engine first.\n' >&2
    printf 'See https://docs.docker.com/get-docker/\n' >&2
    return 1
  fi

  if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but not running. Start Docker Desktop or the Docker daemon, then try again.\n' >&2
    return 1
  fi
}

resolve_compose_file() {
  local mode="${PODOCRACY_LAUNCH_MODE:-auto}"
  local images_file="${PODOCRACY_COMPOSE_IMAGES_FILE:-docker-compose.images.yml}"
  local source_file="${PODOCRACY_COMPOSE_SOURCE_FILE:-docker-compose.yml}"

  case "$mode" in
    images)
      printf '%s\n' "$images_file"
      ;;
    source)
      printf '%s\n' "$source_file"
      ;;
    auto)
      if [[ -f "$images_file" ]]; then
        printf '%s\n' "$images_file"
      else
        printf '%s\n' "$source_file"
      fi
      ;;
    *)
      printf 'Unknown PODOCRACY_LAUNCH_MODE: %s (use auto, images, or source)\n' "$mode" >&2
      return 1
      ;;
  esac
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    printf 'Docker Compose is not available. Install the Compose plugin or docker-compose.\n' >&2
    return 1
  fi
}

compose_up() {
  local compose_file="$1"
  local build_flag="${2:-}"

  if [[ ! -f "$compose_file" ]]; then
    printf 'Compose file not found: %s\n' "$compose_file" >&2
    return 1
  fi

  if [[ "${PODOCRACY_PULL_IMAGES:-0}" == "1" ]]; then
    printf 'Pulling images from %s...\n' "$compose_file"
    compose_cmd -f "$compose_file" pull
  fi

  if [[ "$build_flag" == "--build" ]]; then
    printf 'Starting Podocracy Worker Portal from %s (build)...\n' "$compose_file"
    compose_cmd -f "$compose_file" up --build -d
  else
    printf 'Starting Podocracy Worker Portal from %s...\n' "$compose_file"
    compose_cmd -f "$compose_file" up -d
  fi
}

wait_for_portal() {
  local url="$1"
  local timeout="${PODOCRACY_LAUNCH_TIMEOUT:-90}"

  if ! command -v curl >/dev/null 2>&1; then
    printf 'curl is not installed; skipping readiness check.\n' >&2
    sleep 3
    return 0
  fi

  printf 'Waiting for %s/api/health' "$url"
  local attempt=1
  while [[ "$attempt" -le "$timeout" ]]; do
    if curl -fsS "${url}/api/health" >/dev/null 2>&1; then
      printf '\nPortal is ready: %s\n' "$url"
      return 0
    fi
    printf '.'
    sleep 1
    attempt=$((attempt + 1))
  done

  printf '\nPortal did not become ready in time. Check logs with: docker compose logs\n' >&2
  return 1
}

open_portal() {
  local url="$1"

  if [[ "${PODOCRACY_NO_BROWSER:-0}" == "1" ]]; then
    printf 'Open this URL in your browser: %s\n' "$url"
    return 0
  fi

  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v wslview >/dev/null 2>&1; then
    wslview "$url" >/dev/null 2>&1 || true
  else
    printf 'Open this URL in your browser: %s\n' "$url"
  fi
}
