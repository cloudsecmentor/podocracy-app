# Local Setup

1. Install Docker Desktop or a compatible Docker runtime.
2. Point Compose at an env file with provider keys:

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/.env
```

3. Start the portal:

```bash
docker compose up --build
```

4. Open `http://localhost:8080`.

Generated files live under `data/projects/`. Do not commit this directory.
