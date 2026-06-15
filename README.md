# Podocracy Worker Portal

Self-hosted Docker Compose portal for creating local voiceover translation projects.

## Local Run

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/provider.env
docker compose up --build
```

Open `http://localhost:8080`.

The stack writes projects, logs, and artifacts under `data/projects/`. Provider keys are read from the env file and are not written to project folders.

## Test Upload

Use the web UI, or call the API through the web proxy:

```bash
curl -F "source=@/path/to/file.mp3" \
  -F "language=RU" \
  -F "voice=alloy" \
  http://localhost:8080/api/projects
```

The worker processes one queued project at a time. Final files appear in the project's `output/` folder and in the UI artifact list.

## Remote Ubuntu

See [docs/remote-ubuntu-setup.md](docs/remote-ubuntu-setup.md).
