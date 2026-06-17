# Podocracy Worker Portal

Self-hosted Docker Compose portal for creating local voiceover translation projects.

## Quick Start With Prebuilt Images

Install Docker first:

- macOS/Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Linux: [Docker Engine](https://docs.docker.com/engine/install/)

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

Start the portal:

```bash
docker compose --env-file .env -f docker-compose.images.yml up -d
```

Open `http://localhost:8080`.

To update to a newer image later, run the same command again with `pull` first:

```bash
docker compose --env-file .env -f docker-compose.images.yml pull
docker compose --env-file .env -f docker-compose.images.yml up -d
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
export PODOCRACY_IMAGE_TAG=v0.1.0

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
