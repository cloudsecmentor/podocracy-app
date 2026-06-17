# Releases and Prebuilt Images

The project supports two distribution modes:

- Source build: users clone the repo and run `docker compose up --build`.
- Prebuilt images: users run `docker-compose.images.yml` and pull published images from a container registry.

## Release Manifest

`release.yaml` is the release source of truth:

```yaml
version: v0.1.0
latest: true
```

Updating `version` on `main` triggers `.github/workflows/release.yml`. The workflow:

1. Validates that `version` looks like `v0.1.0`.
2. Creates an annotated Git tag with that exact version if it does not exist.
3. Refuses to move the tag if it already exists on another commit.
4. Builds and pushes:

```text
ghcr.io/<owner>/podocracy-web:<version>
ghcr.io/<owner>/podocracy-api:<version>
ghcr.io/<owner>/podocracy-worker:<version>
```

5. Also pushes `latest` for each image when `latest: true`.
6. Creates a GitHub Release for the version.

Version tags are immutable. Do not reuse `v0.1.0` for a changed build; publish `v0.1.1`.

## Publishing a Release

Edit `release.yaml`:

```yaml
version: v0.1.1
latest: true
```

Commit and push the change to `main`:

```bash
git add release.yaml
git commit -m "Release v0.1.1"
git push origin main
```

The workflow creates the Git tag and image tags. If a release build fails after the tag is created, rerunning the workflow is allowed as long as the tag points to the same commit.

For public distribution through GHCR, make the published packages public in the GitHub package settings. If the packages remain private, users must run `docker login ghcr.io` with credentials that can read the images.

## Running Prebuilt Images

For this repository, the default GHCR namespace is `cloudsecmentor`.

Standalone install folder:

```bash
mkdir -p "$HOME/podocracy-worker-portal/projects"
cd "$HOME/podocracy-worker-portal"
curl -fsSLO https://raw.githubusercontent.com/cloudsecmentor/podocracy-app/main/docker-compose.images.yml
```

Create `.env` in that folder:

```env
OPENAI_API_KEY=replace-with-your-openai-key
DEEPL_AUTH_KEY=
ELEVENLABS_API_KEY=

PORTAL_HTTP_PORT=8080
PORTAL_ADMIN_PASSWORD=
WORKER_POLL_SECONDS=3

PODOCRACY_PROJECTS_DIR=./projects
PODOCRACY_IMAGE_TAG=v0.1.1

OPENAI_TRANSCRIBE_MODEL=whisper-1
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=alloy
```

Run:

```bash
docker compose --env-file .env -f docker-compose.images.yml up -d
```

Pinned release:

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/provider.env
export PODOCRACY_IMAGE_TAG=v0.1.1
export PODOCRACY_PROJECTS_DIR=/absolute/path/to/podocracy-projects

docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

Moving latest release:

```bash
export PODOCRACY_IMAGE_TAG=latest
docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

Pinned versions are better for support and reproducibility. `latest` is useful for quick testing, but it changes over time.

## Project File Persistence

The app stores project files under `/data/projects` inside the API and worker containers. Compose maps that path to a host directory:

```yaml
${PODOCRACY_PROJECTS_DIR:-./data/projects}:/data/projects
```

Project files survive:

- `docker compose restart`
- `docker compose down`
- image pulls
- container recreation
- source rebuilds
- switching from `v0.1.0` to `v0.1.1`

Project files do not automatically follow users to a different checkout folder when `PODOCRACY_PROJECTS_DIR` is left at the default `./data/projects`. For durable installs, set `PODOCRACY_PROJECTS_DIR` to an absolute path outside the repo, for example:

```bash
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
```

Keep provider keys in the env file. Do not store `.env` inside a public release bundle.
