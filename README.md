# Podocracy Worker Portal

Self-hosted Docker Compose portal for creating local voiceover translation projects.

## Quick Start With Prebuilt Images

Install Docker first:

- macOS/Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- macOS with [Colima](https://github.com/abiosoft/colima): Docker Engine + Compose plugin (see below)
- Linux: [Docker Engine](https://docs.docker.com/engine/install/)

Prebuilt images support **linux/amd64** and **linux/arm64** (Apple Silicon) from **v0.2.1** onward.

Create an app folder:

```bash
mkdir -p "$HOME/podocracy-worker-portal/projects"
cd "$HOME/podocracy-worker-portal"
```

Create your `.env` by copying [.env.example](.env.example), then fill in at least:

- `OPENAI_API_KEY` (required)
- `PORTAL_ADMIN_PASSWORD` (strongly recommended whenever the portal is reachable from other machines)

`.env.example` is the source of truth for supported provider keys (OpenAI, DeepL, ElevenLabs) and common runtime options.

Download the prebuilt-image Compose file:

```bash
curl -fsSLO https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main/docker-compose.images.yml
```

Start the portal:

```bash
docker compose -f docker-compose.images.yml up -d
```

Open `http://localhost:8080`.

First startup can take a while: Docker may pull large images, and the worker may spend extra time caching Whisper artifacts before the first job completes.

### One-click launch

From the app folder (where your compose file and `.env` live), you can start the portal and open your browser in one step:

**macOS / Linux:**

```bash
RAW_BASE="https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main"
curl -fsSLO "$RAW_BASE/scripts/launch.sh"
curl -fsSLO "$RAW_BASE/scripts/_launch-common.sh"
chmod +x launch.sh
./launch.sh
```

Download both files into the same folder as your `docker-compose.images.yml` and `.env`. If you cloned this repo, run `./scripts/launch.sh` instead.

**Windows:**

```bat
set "RAW_BASE=https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main"
curl -fsSLO "%RAW_BASE%/scripts/launch.bat"
launch.bat
```

The launcher checks that Docker is running, starts the stack, waits for `/api/health`, then opens `http://localhost:8080`.

Launcher-specific environment variables:

- `PODOCRACY_LAUNCH_MODE=auto|images|source` (default `auto`)
- `PODOCRACY_PULL_IMAGES=1` pull newer images before starting
- `PODOCRACY_NO_BROWSER=1` start containers without opening a browser tab
- `PODOCRACY_LAUNCH_TIMEOUT=90` readiness timeout in seconds
- `PODOCRACY_COMPOSE_IMAGES_FILE=docker-compose.images.yml`
- `PODOCRACY_COMPOSE_SOURCE_FILE=docker-compose.yml`
- `PORTAL_HTTP_PORT=8080` local HTTP port

### Install as a desktop app (PWA)

After the portal is running, open it in Chrome, Edge, or Safari and use the browser's **Install app** option (or **Add to Dock** on macOS).

### Update / Stop / Restart

Update to a newer image:

```bash
docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

Stop:

```bash
docker compose -f docker-compose.images.yml down
```

If you cloned this repo and run from source, you can also use:

- `./scripts/stop-local.sh`
- `./scripts/restart-local.sh`

### Docker Compose CLI (Colima and older setups)

Use whichever Compose command works on your machine. Both read the same `docker-compose.images.yml`.

Preferred:

```bash
docker compose -f docker-compose.images.yml up -d
```

Fallback:

```bash
docker-compose -f docker-compose.images.yml up -d
```

On Colima, start the daemon first:

```bash
colima start
```

## Environment Variables Reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Required for OpenAI-backed transcription/TTS/translation paths. |
| `DEEPL_AUTH_KEY` | empty | Enables DeepL translation provider. |
| `ELEVENLABS_API_KEY` | empty | Enables ElevenLabs TTS provider. |
| `PORTAL_ADMIN_PASSWORD` | empty | Optional HTTP basic auth password for the web portal; set this for any non-local exposure. |
| `PORTAL_HTTP_PORT` | `8080` | Host port mapped to the portal web container. |
| `PODOCRACY_ENV_FILE` | `.env` | Env file loaded by API and worker containers. |
| `PODOCRACY_PROJECTS_DIR` | images: `./projects`; source: `./data/projects` | Host path for project files, logs, and artifacts. |
| `WORKER_POLL_SECONDS` | `3` | Worker queue polling interval. |
| `PODOCRACY_IMAGE_REGISTRY` | `ghcr.io` | Image registry for prebuilt stacks. |
| `PODOCRACY_IMAGE_NAMESPACE` | `cloudsecmentor` | Image namespace/owner for prebuilt stacks. |
| `PODOCRACY_IMAGE_TAG` | `latest` | Image tag for prebuilt stacks. |
| `PODOCRACY_LAUNCH_MODE` | `auto` | Launcher mode selection (`auto`, `images`, `source`). |
| `PODOCRACY_PULL_IMAGES` | `0` | If `1`, launcher runs `docker compose pull` before `up -d`. |
| `PODOCRACY_NO_BROWSER` | `0` | If `1`, launcher does not open the browser automatically. |
| `PODOCRACY_LAUNCH_TIMEOUT` | `90` | Launcher health-check timeout in seconds. |
| `PODOCRACY_COMPOSE_IMAGES_FILE` | `docker-compose.images.yml` | Images compose file path for launchers. |
| `PODOCRACY_COMPOSE_SOURCE_FILE` | `docker-compose.yml` | Source compose file path for launchers. |

Env file rule:

- If `.env` is next to your compose file, Compose loads it automatically.
- Use `--env-file` only when your env file lives elsewhere.

## Local Source Run

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/provider.env
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
./scripts/start-local.sh
```

Open `http://localhost:8080`.

## Prebuilt Images From Repo Checkout

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/provider.env
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
export PODOCRACY_IMAGE_TAG=v0.2.1

docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

See [docs/releases.md](docs/releases.md) for release tagging and image publishing.

## Troubleshooting

- **Port 8080 already in use:** set `PORTAL_HTTP_PORT` (for example `PORTAL_HTTP_PORT=8081`) and restart.
- **Launcher times out waiting for health:** increase `PODOCRACY_LAUNCH_TIMEOUT` and check logs.
- **Where to see logs:**
  - Stack logs: `docker compose -f docker-compose.images.yml logs`
  - Project logs: per-project `logs/` under `PODOCRACY_PROJECTS_DIR`

## Test Upload

Use the web UI, or call the API through the web proxy:

```bash
curl -F "source=@/path/to/file.mp3" \
  -F "language=EN" \
  -F "voice=alloy" \
  http://localhost:8080/api/projects
```

## Remote Ubuntu

See [docs/remote-ubuntu-setup.md](docs/remote-ubuntu-setup.md) and keep `PORTAL_ADMIN_PASSWORD` set when exposing the portal remotely.

## License

Licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) (noncommercial use only).
