---
name: podocracy-worker-portal
description: Use when deploying, testing, or operating the Podocracy Docker Compose worker portal locally or on a private Ubuntu VM, including project-folder voiceover jobs, provider-key env files, Azure VM setup, and end-to-end MP3 translation checks.
metadata:
  short-description: Deploy and test the Podocracy worker portal
---

# Podocracy Worker Portal

Use this skill for the self-hosted Docker Compose portal in `podocracy-app`.

## Required Inputs

- Repo path for the local `podocracy-app` checkout.
- Provider env file path. Do not print secret values.
- Optional test MP3 path.
- Optional Azure resource group and subscription for remote Ubuntu tests.

## Local Workflow

1. Confirm the env file exists and includes `OPENAI_API_KEY` and `DEEPL_AUTH_KEY` by checking variable names only.
2. Start the stack with:

```bash
export PODOCRACY_ENV_FILE=/absolute/path/to/.env
docker compose up --build
```

3. Open `http://localhost:8080` or use the API through the web proxy.
4. Create a project by uploading an MP3 and setting `language=RU`.
5. Watch `data/projects/<project>/status.json` until `completed` or `failed`.
6. Verify `output/source.voiceover.mp3`, `manifest.json`, and `logs/orchestrator.log`.

## Remote Ubuntu Workflow

1. Use `az account set --subscription <subscription-id>`.
2. Create an Ubuntu VM in the requested resource group.
3. Install Docker and the Compose plugin.
4. Copy the repo and provider env file to the VM.
5. Start with:

```bash
export PODOCRACY_ENV_FILE=/opt/podocracy-worker-portal/.env
export PORTAL_ADMIN_PASSWORD='<strong password>'
docker compose up --build -d
```

6. Check `curl http://localhost:8080/api/health` over SSH.
7. Upload the test MP3 through the API or portal and verify `source.voiceover.mp3`.

## Safety Rules

- Never copy or commit `.env`, `__env__`, logs, generated artifacts, Azure subscription IDs, storage connection strings, or personal-path examples into public docs.
- Keep provider keys in env files or Docker secrets only.
- Do not print API keys, tokens, or full env values.
- Before sharing the repo, run a secret scan and a personal-path scan.
