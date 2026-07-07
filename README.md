# Podocracy Worker Portal

Self-hosted Docker Compose portal for creating local voiceover translation projects.

## Quick Start With Prebuilt Images

Install Docker first:

- macOS/Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- macOS with [Colima](https://github.com/abiosoft/colima): Docker Engine + Compose plugin (see below)
- Linux: [Docker Engine](https://docs.docker.com/engine/install/)

Prebuilt images support **linux/amd64** and **linux/arm64** (Apple Silicon) from **v0.2.1** onward. Use the same image tag on Intel and ARM Macs; Docker pulls the matching architecture.

Create an app folder:

```bash
mkdir -p "$HOME/podocracy-worker-portal/projects"
cd "$HOME/podocracy-worker-portal"
```

Create `.env` in that folder:

```env
OPENAI_API_KEY=replace-with-your-openai-key
```

Download the prebuilt-image Compose file:

```bash
curl -fsSLO https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main/docker-compose.images.yml
```

Start the portal (with `.env` in this folder, `--env-file` is not required):

```bash
docker compose -f docker-compose.images.yml up -d
```

Open `http://localhost:8080`.

### One-click launch

From the app folder (where your compose file and `.env` live), you can start the portal and open your browser in one step:

**macOS / Linux:**

```bash
curl -fsSLO https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main/scripts/launch.sh
curl -fsSLO https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main/scripts/_launch-common.sh
chmod +x launch.sh
./launch.sh
```

Download both files into the same folder as your `docker-compose.images.yml` and `.env`. If you cloned this repo, run `./scripts/launch.sh` instead.

**Windows:**

```bat
curl -fsSLO https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main/scripts/launch.bat
launch.bat
```

The launcher checks that Docker is running, starts the stack, waits for `/api/health`, then opens `http://localhost:8080`.

Optional environment variables:

- `PODOCRACY_LAUNCH_MODE=source` — build from local source (`docker-compose.yml`) instead of prebuilt images
- `PODOCRACY_PULL_IMAGES=1` — pull newer images before starting
- `PODOCRACY_NO_BROWSER=1` — start containers without opening a browser tab
- `PORTAL_HTTP_PORT` — change the local port (default `8080`)

### Install as a desktop app (PWA)

After the portal is running, open it in Chrome, Edge, or Safari and use the browser’s **Install app** option (or **Add to Dock** on macOS). Podocracy opens in its own window with the same UI, while Docker continues to run the backend.

To update to a newer image later, run the same command again with `pull` first:

```bash
docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

### Docker Compose CLI (Colima and older setups)

Use whichever Compose command works on your machine. Both read the same `docker-compose.images.yml`.

**Preferred:** Docker Compose v2 plugin:

```bash
docker compose -f docker-compose.images.yml up -d
```

If `docker compose` fails (for example `unknown flag: --env-file`), install the plugin:

```bash
brew install docker-compose
docker compose version
```

**Fallback:** standalone `docker-compose` (common on Colima installs):

```bash
docker-compose -f docker-compose.images.yml up -d
```

Provider keys are loaded from `.env` by the API and worker services automatically. Keep `.env` next to the compose file, or set `PODOCRACY_ENV_FILE` to an absolute path. Use `--env-file .env` only when you need Compose itself to read variables from a file in a different location (for example `PODOCRACY_IMAGE_TAG`).

On Colima, start the Docker daemon before pulling images:

```bash
colima start
```

Project files stay in `./projects` by default. Set `PODOCRACY_PROJECTS_DIR` in `.env` only if you want a different storage path.

## Local Source Run

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/provider.env
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
./scripts/start-local.sh
```

Open `http://localhost:8080`.

The stack writes projects, logs, and artifacts under `PODOCRACY_PROJECTS_DIR`. If unset, it falls back to `data/projects/` inside the repo. Provider keys are read from the env file and are not written to project folders.

## Prebuilt Images From Repo Checkout

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/provider.env
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
export PODOCRACY_IMAGE_TAG=v0.2.1

docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

See [docs/releases.md](docs/releases.md) for release tagging and image publishing.

## Test Upload

Use the web UI, or call the API through the web proxy:

```bash
curl -F "source=@/path/to/file.mp3" \
  -F "language=EN" \
  -F "voice=alloy" \
  http://localhost:8080/api/projects
```

The worker processes one queued project at a time. Final files appear in the project's `output/` folder and in the UI artifact list.

## Remote Ubuntu

See [docs/remote-ubuntu-setup.md](docs/remote-ubuntu-setup.md).

## License

Licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

Free for any **noncommercial** purpose — personal, hobby, research, education, and noncommercial organizations. **Commercial use** (including running it as a paid service for others) is **not** permitted without a separate license.

This is a source-available, non-OSI license, and it covers only this project's own code. Third-party dependencies (e.g. ffmpeg, Python packages in `worker/requirements.txt`, and any external APIs such as OpenAI/Whisper) remain under their own terms.
