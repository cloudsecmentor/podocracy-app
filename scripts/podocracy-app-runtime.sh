#!/usr/bin/env bash
# Podocracy desktop runtime (macOS).
#
# This is the no-terminal launcher that a double-clicked Podocracy.app runs. It:
#   1. makes sure Docker Desktop is installed and running,
#   2. creates the app-home folder (~/Podocracy) and downloads the compose file,
#   3. asks for the OpenAI API key on first run and writes .env for the user,
#   4. starts the containers, waits for /api/health, opens the browser, and quits.
#
# It reuses the tested compose/health/launch logic in scripts/_launch-common.sh, and
# adds macOS GUI dialogs (osascript) so a beginner never has to touch a terminal.
#
# It also works when run directly from a repo checkout: `bash scripts/podocracy-app-runtime.sh`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/_launch-common.sh" ]]; then
  # shellcheck source=scripts/_launch-common.sh
  source "$SCRIPT_DIR/_launch-common.sh"
else
  osascript -e 'display dialog "Podocracy is missing an internal file (_launch-common.sh) and cannot start." with title "Podocracy" buttons {"OK"} default button "OK" with icon stop' >/dev/null 2>&1 || true
  exit 1
fi

# Explicit overrides (power users / tests). Empty means "not set by the user".
PODOCRACY_HOME_ENV="${PODOCRACY_HOME:-}"
PODOCRACY_PROJECTS_DIR_ENV="${PODOCRACY_PROJECTS_DIR:-}"
PORTAL_HTTP_PORT_ENV="${PORTAL_HTTP_PORT:-}"

PODOCRACY_HOME=""                    # resolved in resolve_home()
DEFAULT_HOME="$HOME/Podocracy"
PODOCRACY_RAW_BASE="${PODOCRACY_RAW_BASE:-https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main}"
IMAGES_COMPOSE_FILE="${PODOCRACY_COMPOSE_IMAGES_FILE:-docker-compose.images.yml}"
SOURCE_COMPOSE_FILE="${PODOCRACY_COMPOSE_SOURCE_FILE:-docker-compose.yml}"
COMPOSE_FILE="$IMAGES_COMPOSE_FILE"  # re-resolved against the chosen folder in ensure_home()
PORT="${PORTAL_HTTP_PORT:-8080}"
URL="http://localhost:${PORT}"
DOCKER_DOWNLOAD_URL="https://www.docker.com/products/docker-desktop/"
OPENAI_KEYS_URL="https://platform.openai.com/api-keys"

# Remembers which folder the user picked, so we only ask on the very first run.
CONFIG_DIR="${PODOCRACY_CONFIG_DIR:-$HOME/.config/podocracy}"
CONFIG_FILE="$CONFIG_DIR/home.path"
LOG_FILE=""                          # set once PODOCRACY_HOME is known

# ----------------------------------------------------------------------------
# macOS GUI helpers (osascript). Messages are escaped for AppleScript strings.
# ----------------------------------------------------------------------------
escape_osa() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

gui_alert() {
  local title message
  title="$(escape_osa "$1")"
  message="$(escape_osa "$2")"
  osascript >/dev/null 2>&1 <<OSA || true
display dialog "${message}" with title "${title}" buttons {"OK"} default button "OK" with icon caution
OSA
}

# gui_confirm TITLE MESSAGE OK_LABEL CANCEL_LABEL -> 0 when OK pressed.
gui_confirm() {
  local title message ok cancel out
  title="$(escape_osa "$1")"
  message="$(escape_osa "$2")"
  ok="$(escape_osa "${3:-OK}")"
  cancel="$(escape_osa "${4:-Cancel}")"
  out="$(osascript 2>/dev/null <<OSA || true
button returned of (display dialog "${message}" with title "${title}" buttons {"${cancel}", "${ok}"} default button "${ok}")
OSA
)"
  [[ "$out" == "${3:-OK}" ]]
}

# gui_prompt_secret TITLE MESSAGE -> prints entered text; returns 1 if cancelled.
gui_prompt_secret() {
  local title message out
  title="$(escape_osa "$1")"
  message="$(escape_osa "$2")"
  out="$(osascript 2>/dev/null <<OSA
text returned of (display dialog "${message}" with title "${title}" default answer "" with hidden answer buttons {"Cancel", "Save"} default button "Save")
OSA
)" || return 1
  printf '%s' "$out"
}

# gui_choose TITLE MESSAGE DEFAULT BTN1 [BTN2] [BTN3] -> prints the pressed button label
# (empty if the user cancels). AppleScript allows at most three buttons.
gui_choose() {
  local title message def list b
  title="$(escape_osa "$1")"
  message="$(escape_osa "$2")"
  def="$(escape_osa "$3")"
  shift 3
  list=""
  for b in "$@"; do
    b="$(escape_osa "$b")"
    if [[ -z "$list" ]]; then list="\"$b\""; else list="$list, \"$b\""; fi
  done
  osascript 2>/dev/null <<OSA || true
button returned of (display dialog "${message}" with title "${title}" buttons {${list}} default button "${def}")
OSA
}

# choose_folder -> prints a POSIX path chosen by the user; returns 1 if cancelled.
choose_folder() {
  local p
  p="$(osascript 2>/dev/null <<'OSA'
POSIX path of (choose folder with prompt "Select your existing Podocracy folder")
OSA
)" || return 1
  printf '%s' "${p%/}"
}

notify() {
  local message
  message="$(escape_osa "$1")"
  osascript -e "display notification \"${message}\" with title \"Podocracy\"" >/dev/null 2>&1 || true
}

run_in_terminal() {
  local cmd
  cmd="$(escape_osa "$1")"
  osascript >/dev/null 2>&1 <<OSA || true
tell application "Terminal"
  activate
  do script "${cmd}"
end tell
OSA
}

# ----------------------------------------------------------------------------
# Home-folder resolution (supports adopting an existing CLI setup)
# ----------------------------------------------------------------------------

# read_env_value FILE KEY -> prints the value of KEY=... from a .env file (last wins).
read_env_value() {
  local file="$1" key="$2" line val
  [[ -f "$file" ]] || return 0
  line="$(grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | tail -n1 || true)"
  [[ -z "$line" ]] && return 0
  val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  printf '%s' "$val"
}

# Does a folder already look like a Podocracy setup?
is_podocracy_dir() {
  local d="$1"
  [[ -f "$d/.env" || -f "$d/$IMAGES_COMPOSE_FILE" || -f "$d/$SOURCE_COMPOSE_FILE" ]]
}

# Print the first known folder that already contains a Podocracy setup, if any.
detect_existing_home() {
  local c
  for c in "$HOME/podocracy-worker-portal" "$HOME/Podocracy" "$HOME/podocracy"; do
    if is_podocracy_dir "$c"; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 0
}

save_home() {
  mkdir -p "$CONFIG_DIR" 2>/dev/null || true
  printf '%s\n' "$PODOCRACY_HOME" > "$CONFIG_FILE" 2>/dev/null || true
}

first_run_choose_home() {
  local found choice
  found="$(detect_existing_home)"

  if [[ -n "$found" ]]; then
    choice="$(gui_choose "Set up Podocracy" \
      "Found an existing Podocracy setup at: $found. Use it, pick a different folder, or create a new one at $DEFAULT_HOME?" \
      "Use this one" "Use this one" "Choose another..." "Create new")"
    case "$choice" in
      "Use this one") PODOCRACY_HOME="$found" ;;
      "Choose another...") PODOCRACY_HOME="$(choose_folder || printf '%s' "$DEFAULT_HOME")" ;;
      "Create new") PODOCRACY_HOME="$DEFAULT_HOME" ;;
      *) PODOCRACY_HOME="$found" ;;
    esac
  else
    choice="$(gui_choose "Set up Podocracy" \
      "Do you already have a Podocracy folder (for example from the command-line setup)? Choose it, or create a new one at $DEFAULT_HOME." \
      "Create new" "Choose existing..." "Create new")"
    case "$choice" in
      "Choose existing...") PODOCRACY_HOME="$(choose_folder || printf '%s' "$DEFAULT_HOME")" ;;
      *) PODOCRACY_HOME="$DEFAULT_HOME" ;;
    esac
  fi

  [[ -z "$PODOCRACY_HOME" ]] && PODOCRACY_HOME="$DEFAULT_HOME"
  save_home
}

resolve_home() {
  # 1. Explicit env override wins and is not persisted.
  if [[ -n "$PODOCRACY_HOME_ENV" ]]; then
    PODOCRACY_HOME="$PODOCRACY_HOME_ENV"
    return 0
  fi
  # 2. Remembered choice from a previous run.
  if [[ -f "$CONFIG_FILE" ]]; then
    local saved
    saved="$(head -n1 "$CONFIG_FILE" 2>/dev/null || true)"
    if [[ -n "$saved" && -d "$saved" ]]; then
      PODOCRACY_HOME="$saved"
      return 0
    fi
  fi
  # 3. First run: ask the user (auto-detecting an existing setup).
  first_run_choose_home
}

# Respect an existing setup's projects dir / port instead of forcing our defaults.
setup_projects_dir() {
  if [[ -n "$PODOCRACY_PROJECTS_DIR_ENV" ]]; then
    export PODOCRACY_PROJECTS_DIR="$PODOCRACY_PROJECTS_DIR_ENV"
    return 0
  fi
  local from_env
  from_env="$(read_env_value "$PODOCRACY_HOME/.env" PODOCRACY_PROJECTS_DIR)"
  if [[ -n "$from_env" ]]; then
    # The adopted .env already defines it; let Compose read it from .env.
    unset PODOCRACY_PROJECTS_DIR 2>/dev/null || true
  else
    export PODOCRACY_PROJECTS_DIR="$PODOCRACY_HOME/projects"
  fi
}

setup_port() {
  if [[ -n "$PORTAL_HTTP_PORT_ENV" ]]; then
    PORT="$PORTAL_HTTP_PORT_ENV"
    URL="http://localhost:${PORT}"
    return 0
  fi
  local p
  p="$(read_env_value "$PODOCRACY_HOME/.env" PORTAL_HTTP_PORT)"
  if [[ -n "$p" ]]; then
    PORT="$p"
    URL="http://localhost:${PORT}"
  fi
}

# ----------------------------------------------------------------------------
# Setup steps
# ----------------------------------------------------------------------------
ensure_docker_desktop() {
  if ! command -v docker >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1 && gui_confirm \
      "Docker is required" \
      "Podocracy runs on Docker Desktop, which isn't installed yet. Install it now with Homebrew? A Terminal window will show progress; this can take several minutes." \
      "Install with Homebrew" "Open download page"; then
      run_in_terminal "brew install --cask docker"
      gui_alert "Finish Docker setup" "When Homebrew finishes installing Docker Desktop, open it once (accept its prompts), then open Podocracy again."
    else
      gui_alert "Docker is required" "Podocracy needs Docker Desktop. We'll open the download page now. Install Docker, start it once, then open Podocracy again."
      open "$DOCKER_DOWNLOAD_URL" >/dev/null 2>&1 || true
    fi
    exit 0
  fi

  if ! docker info >/dev/null 2>&1; then
    notify "Starting Docker Desktop…"
    open -a Docker >/dev/null 2>&1 || true
    local waited=0 timeout="${PODOCRACY_DOCKER_TIMEOUT:-120}"
    until docker info >/dev/null 2>&1; do
      sleep 2
      waited=$((waited + 2))
      if (( waited >= timeout )); then
        gui_alert "Docker didn't start" "Docker Desktop didn't finish starting in time. Open Docker Desktop, wait for the whale icon to stop animating, then open Podocracy again."
        exit 1
      fi
    done
  fi
}

ensure_home() {
  if ! mkdir -p "$PODOCRACY_HOME/projects" "$PODOCRACY_HOME/logs" 2>/dev/null; then
    gui_alert "Setup failed" "Podocracy couldn't create or open its folder at: $PODOCRACY_HOME"
    exit 1
  fi

  # Only download the images compose file when the folder has no compose file at all,
  # so we don't clobber an adopted setup (images or source checkout).
  if [[ ! -f "$PODOCRACY_HOME/$IMAGES_COMPOSE_FILE" && ! -f "$PODOCRACY_HOME/$SOURCE_COMPOSE_FILE" ]]; then
    notify "Downloading Podocracy configuration…"
    if ! curl -fsSL "$PODOCRACY_RAW_BASE/$IMAGES_COMPOSE_FILE" -o "$PODOCRACY_HOME/$IMAGES_COMPOSE_FILE"; then
      gui_alert "Download failed" "Couldn't download the Podocracy configuration. Check your internet connection and open Podocracy again."
      exit 1
    fi
  fi

  # Prefer the images compose file, fall back to a source checkout's compose file.
  if [[ -f "$PODOCRACY_HOME/$IMAGES_COMPOSE_FILE" ]]; then
    COMPOSE_FILE="$IMAGES_COMPOSE_FILE"
  else
    COMPOSE_FILE="$SOURCE_COMPOSE_FILE"
  fi
}

write_env() {
  local key="$1"
  cat > "$PODOCRACY_HOME/.env" <<ENV
# Written by the Podocracy first-run setup. Edit here to change keys later.
OPENAI_API_KEY=${key}
DEEPL_AUTH_KEY=
ELEVENLABS_API_KEY=

PORTAL_HTTP_PORT=${PORT}
PORTAL_ADMIN_PASSWORD=
WORKER_POLL_SECONDS=3

OPENAI_TRANSCRIBE_MODEL=whisper-1
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=alloy
ENV
  chmod 600 "$PODOCRACY_HOME/.env" 2>/dev/null || true
}

ensure_env() {
  [[ -f "$PODOCRACY_HOME/.env" ]] && return 0

  gui_alert "Welcome to Podocracy" "First, let's add your OpenAI API key so Podocracy can transcribe and voice your projects. You can get a key at ${OPENAI_KEYS_URL} — the next box keeps it hidden as you paste."

  local key
  if ! key="$(gui_prompt_secret "Podocracy setup" "Paste your OpenAI API key (leave blank to add it later):")"; then
    gui_alert "Setup paused" "No problem — open Podocracy again whenever you're ready to add your OpenAI API key."
    exit 0
  fi

  write_env "$key"

  if [[ -z "$key" ]]; then
    gui_alert "Add your key later" "Podocracy will start, but jobs need an OpenAI API key. Add it any time in: $PODOCRACY_HOME/.env"
  fi
}

launch_stack() {
  notify "Starting Podocracy… the first launch can take a few minutes."
  if ! compose_up "$COMPOSE_FILE"; then
    gui_alert "Couldn't start Podocracy" "Starting the containers failed. Details are in the log file: $LOG_FILE"
    exit 1
  fi

  if ! wait_for_portal "$URL"; then
    gui_alert "Almost ready" "Podocracy is taking longer than usual, likely still downloading components on first run. We'll open the page now — refresh in a minute if it isn't ready yet."
  fi

  open "$URL" >/dev/null 2>&1 || true
  notify "Podocracy is ready."
}

main() {
  ensure_docker_desktop
  resolve_home
  ensure_home
  LOG_FILE="$PODOCRACY_HOME/logs/launch.log"
  cd "$PODOCRACY_HOME"
  setup_projects_dir
  setup_port
  # Route all launcher output to the log now that the home folder exists.
  exec >>"$LOG_FILE" 2>&1
  ensure_env
  launch_stack
  exit 0
}

main "$@"
