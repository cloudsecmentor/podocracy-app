# Local Setup

1. Install Docker Desktop or a compatible Docker runtime.
2. Point Compose at an env file with provider keys:

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/.env
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
```

3. Start the portal:

```bash
docker compose up --build
```

4. Open `http://localhost:8080`.

Generated files live under `PODOCRACY_PROJECTS_DIR`. If unset, they fall back to `data/projects/` inside the repo. Do not commit generated project data. Use an absolute path to keep projects across repo checkout changes and release downloads.

## Prebuilt Images

Use the image Compose file when you do not want to build locally:

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/.env
export PODOCRACY_PROJECTS_DIR="$HOME/podocracy-projects"
export PODOCRACY_IMAGE_NAMESPACE=<github-owner>
export PODOCRACY_IMAGE_TAG=v0.1.0

docker compose -f docker-compose.images.yml pull
docker compose -f docker-compose.images.yml up -d
```

Use `PODOCRACY_IMAGE_TAG=latest` only when you intentionally want the newest published release.
