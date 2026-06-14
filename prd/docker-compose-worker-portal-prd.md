# Podocracy Docker Compose Worker Portal PRD

Date: 2026-06-15

## Summary

This PRD describes a Docker Compose distribution of Podocracy voiceover processing. Instead of Sergey hosting the API and workers in Azure, users run a private worker portal either on their own local machine or on a remote Ubuntu cloud VM. Users provide their own model/provider API keys, upload or select source files through a web UI, run the worker locally to that environment, and download final voiceover artifacts.

This approach is different from the desktop-app PRD. The user-facing app is a web UI served by Docker Compose, and the worker is the existing Linux processing pipeline adapted to run against local project folders instead of Azure Blob Storage and hosted API callbacks.

The same distribution should work in two modes:

- **Local mode:** user runs Docker Compose on their own macOS, Windows, or Linux computer and opens `http://localhost`.
- **Remote mode:** user runs Docker Compose on a private Ubuntu cloud VM and opens the portal over HTTPS.

Remote Ubuntu is the more reliable target. It matches the Linux worker environment, avoids old laptop sleep/resource issues, and lets long jobs continue after the user's laptop disconnects. Local mode is still useful for technical users or small files, but Docker availability on older macOS/Windows machines must be validated.

## Goals

- Package the Podocracy worker as a self-hosted Docker Compose app.
- Provide a simple browser UI for project creation, settings, progress, logs, and artifact download.
- Support both local and remote Ubuntu deployments with the same Compose stack.
- Keep files, outputs, logs, and provider keys under the user's control.
- Avoid requiring Sergey-hosted Azure API, Azure workers, Azure queues, or Azure Blob Storage for normal processing.
- Prepare the repo for possible open-source release by copying only relevant worker/app files and excluding secrets, personal paths, and private deployment details.

## Non-Goals

- Full offline processing. Model APIs still require internet access.
- Hosted multi-tenant SaaS.
- Azure B2C login in the MVP.
- Azure queue dispatch in the MVP.
- Azure Blob Storage as required project storage in the MVP.
- Native desktop app packaging in the MVP.
- Autonomous agent repair as the core user experience.
- Full DeepSeek replacement of transcription, translation, and TTS in the MVP.

## Target Users

- A small number of known Podocracy users.
- Users who can run Docker locally, or can rent/use a small Ubuntu cloud VM.
- Users willing to provide their own provider API keys.
- Users who want a private portal rather than relying on Sergey-hosted Azure workers.

## Deployment Modes

### Local Machine Mode

The user installs Docker Desktop or another compatible Docker runtime, runs the Compose stack, and opens the UI in a browser.

Typical access:

- `http://localhost:8080`
- Project files stored in a local mounted folder.
- Provider keys entered through UI setup or `.env`.

Pros:

- No cloud VM required.
- Files stay on the user's machine.
- Easy to test and iterate.

Cons:

- Docker may not work on older macOS/Windows laptops.
- Long jobs stop if the laptop sleeps or Docker exits.
- Local CPU/RAM/disk limitations matter.
- Windows/macOS file sharing and volume permissions can create support issues.

### Remote Ubuntu Mode

The user provisions an Ubuntu VM, installs Docker, runs the Compose stack, and accesses the UI over HTTPS.

Typical access:

- `https://user-domain.example`
- `https://vm-public-ip` during early testing
- Project files stored in a persistent Docker volume or mounted disk.

Pros:

- Best fit for the existing Linux worker.
- Long jobs continue while the user's laptop is off.
- Easier dependency and runtime support.
- More consistent than old Windows/macOS machines.
- Can be backed up with VM snapshots or volume backups.

Cons:

- User must manage or pay for a cloud VM.
- Security matters more because the UI is network-accessible.
- HTTPS, authentication, firewall, updates, and backups become part of the product.

## High-Level Architecture

```text
Browser
  |
  v
Web UI container
  - project wizard
  - settings form
  - provider key setup
  - progress and logs
  - download/export
  |
  v
App API container
  - local project database
  - job queue
  - worker launcher
  - artifact index
  - support bundle builder
  |
  v
Worker container
  - existing processing pipeline
  - local project folder input/output
  - OpenAI, DeepL, optional DeepSeek, optional ElevenLabs
  |
  v
Persistent volumes
  - projects
  - logs
  - config
```

Optional remote-only component:

- Reverse proxy container such as Caddy, Traefik, or Nginx for HTTPS and routing.

## Compose Services

### `web`

Browser UI for users.

Responsibilities:

- First-run setup.
- Project list.
- New project wizard.
- Settings form matching the current Podocracy web flow.
- Provider key status.
- Job progress and logs.
- Artifact preview/download.
- Support bundle download.

Implementation options:

- Reuse React/Next.js patterns from `podocracy-tech`.
- Use a simpler static React/Vite app if the API is local.
- Keep UI generic enough to run at `localhost` or behind a reverse proxy.

### `app-api`

Small local API for the portal.

Responsibilities:

- Store projects and job metadata.
- Write local params JSON.
- Manage job queue and locking.
- Launch worker jobs.
- Track process state.
- Parse worker status/log output.
- Index generated artifacts.
- Build support bundles.
- Store encrypted provider-key references or read keys from environment/secrets.

Preferred storage:

- SQLite for local metadata.
- Filesystem for source files, artifacts, and logs.

### `worker`

Processing runtime based on the existing worker files from `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container`.

Responsibilities:

- Run one project at a time initially.
- Read source media, params JSON, subtitles, and optional manual recording files from a mounted project folder.
- Call model providers using user-provided keys.
- Write intermediate JSON, final audio/video, logs, and an artifact manifest.
- Return explicit success/failure state.

### `proxy`

Optional but recommended for remote Ubuntu mode.

Responsibilities:

- HTTPS termination.
- Basic routing to the web/app service.
- Optional basic auth or forward-auth integration.

Recommended MVP choice:

- Caddy for simplest HTTPS automation on a single-user VM.

## Project Folder Contract

Each project should be a directory under a persistent `projects` volume.

Example:

```text
projects/
  project-2026-06-15-example/
    input/
      source.mp3
      subtitles.srt
    config/
      params.json
      provider-selection.json
    work/
      source.raw.json
      source.combined.json
      source.translated.json
      source.improved.json
    output/
      source.voiceover.mp3
      source.voiceover.mp4
    logs/
      orchestrator.log
      stages/
        preprocess.log
        transcribe.log
        translate.log
        improve.log
        voiceover.log
    manifest.json
    status.json
```

Rules:

- Do not store provider API keys in the project folder.
- Do not require Azure-style blob paths for local processing.
- Preserve source filenames for traceability, but sanitize path handling.
- Write a machine-readable `status.json` after each stage.
- Write a `manifest.json` that lists generated artifacts, stage results, durations, and provider selections.

## Worker Files to Copy Into `podocracy-app`

The open-source candidate repo should contain only the files needed to run the self-hosted worker portal. Copying should be explicit, reviewed, and stripped of private deployment material.

Recommended worker source files to copy or port:

- `backend/processing_container/pd-00-orchestrator.py`
- `backend/processing_container/shared_functions.py`
- `backend/processing_container/parameters.json`
- `backend/processing_container/pd-005-preprocess.py`
- `backend/processing_container/pd-005-url-processing.py`
- `backend/processing_container/pd-007-subtitles.py`
- `backend/processing_container/pd-010-raw-transcribe.py`
- `backend/processing_container/pd-010-02-whisper-api-transcribe.py`
- `backend/processing_container/pd-020-combine.py`
- `backend/processing_container/pd-025-timesync.py`
- `backend/processing_container/pd-030-translate.py`
- `backend/processing_container/pd-035-customize.py`
- `backend/processing_container/pd-040-improve.py`
- `backend/processing_container/pd-050-voiceover.py`
- `backend/processing_container/pd-055-postprocess.py`
- `backend/processing_container/pd-051-ffmpeg-norm.sh`
- `backend/processing_container/shared_clicks_removal.py` if manual recording cleanup is included.
- Shared common helpers needed by the above scripts, especially supported file type and naming helpers.
- Dependency files needed to build the worker image.

Files and content to avoid copying:

- `.env` files.
- `__env__/` examples or backups if they contain real values.
- Azure subscription IDs, tenant IDs, resource group names, storage account names, container names, or Key Vault names.
- Personal filesystem paths.
- Application Insights connection strings.
- API keys or old test keys.
- Private deployment scripts not needed for self-hosted local/remote Compose.
- Logs or generated processing artifacts from real users.

Before open-sourcing, run a secret scan and a personal-path scan across the copied files.

## Worker Versioning and Reuse Plan

Copying worker files into `podocracy-app` is acceptable for the first proof of concept, but it should not become the long-term support model if Sergey also continues running the Azure-hosted Podocracy stack for some time.

The preferred long-term model is one shared, versioned worker artifact used by both hosted and self-hosted deployments.

Target shape:

```text
podocracy-worker Docker image
  shared processing code
  supports --mode azure
  supports --mode local

podocracy-tech Azure deployment
  runs podocracy-worker:<version> --mode azure

podocracy-app Docker Compose portal
  runs podocracy-worker:<version> --mode local
```

Why this is better:

- Bug fixes in transcription, translation, improve, TTS, audio processing, and artifact generation are made once.
- Hosted Azure users and self-hosted users can be compared by worker image version.
- Support can ask for one version string instead of inspecting copied script state.
- The self-hosted repo can remain open-source friendly while the worker release process stays explicit.
- Cloud-specific and local-specific behavior can live behind adapters instead of divergent forks.

Recommended phases:

1. **Prototype phase:** copy the relevant worker files into `podocracy-app/worker/` to move quickly and prove the Docker Compose portal.
2. **Stabilization phase:** extract the copied worker into a standalone `podocracy-worker` source tree or repo with a clear CLI and Dockerfile.
3. **Dual-support phase:** publish versioned worker images, for example `podocracy-worker:0.1.0`, and make both Azure dispatch and Docker Compose portal consume the same image.
4. **Adapter phase:** split the worker into shared processing core plus runtime adapters:
   - `azure` adapter for Azure Blob paths, hosted API callbacks, billing, and queue/VM execution.
   - `local` adapter for project folders, local status files, local logs, and user-owned provider keys.

Avoid:

- Importing worker code directly from the old `voice-over-service` filesystem path.
- Maintaining independent worker copies in `voice-over-service` and `podocracy-app`.
- Publishing images tagged only as `latest`.
- Mixing private Azure deployment scripts, secrets, or personal paths into the open-source app repo.

Versioning requirements:

- Every worker build should expose a version through CLI output, logs, `manifest.json`, and the web UI.
- The Compose file should pin a worker image version by default.
- The UI should show when a newer worker image is available.
- Support bundles should include app version, worker image version, Compose version, and project params schema version.
- Breaking changes to params or artifacts should bump a schema version and include migration notes.

## Worker Refactor Requirements

The current worker can run locally in principle, but it should be refactored before being treated as a user-facing product.

Required changes:

- Add `local-mode` as a first-class runtime mode.
- Make Azure Blob transfer optional and off by default in local mode.
- Make hosted API status callbacks optional and off by default in local mode.
- Replace implicit cloud status with structured local progress events.
- Stop on failed subprocess stage or mark `completed_with_errors`.
- Write per-stage result metadata.
- Write an artifact manifest.
- Make params JSON required or validate missing params explicitly instead of silently falling back to unrelated defaults.
- Guard or remove runtime `apt-get` behavior from app runs; dependencies should be installed at image build time.
- Guard platform-specific helper calls.
- Add `yt-dlp` to the worker image if URL ingestion is supported.
- Add provider abstraction for chat-completion stages before exposing DeepSeek as a normal option.
- Ensure support bundles redact secrets.

## Provider Keys and Models

MVP provider setup:

- OpenAI key required for current Whisper API, text stages, and OpenAI TTS.
- DeepL key required for current translation stage.
- ElevenLabs key optional for alternate TTS.
- DeepSeek optional only for text stages after provider abstraction exists.

DeepSeek caveat:

- DeepSeek can plausibly replace some chat-completion text work such as `customize` and `improve`.
- DeepSeek does not replace Whisper transcription, DeepL translation, or OpenAI/ElevenLabs TTS without additional provider changes.

Key handling:

- Local mode may read keys from Docker secrets, `.env`, or the app key store.
- Remote mode should prefer Docker secrets or encrypted app config.
- Never write keys into params JSON, logs, project folders, support bundles, screenshots, or generated artifacts.
- Show users a clear warning that provider usage may incur costs.

## Security Requirements

Local-only mode:

- Bind UI to `127.0.0.1` by default.
- Do not expose ports publicly unless the user opts in.
- Keep provider keys out of logs and support bundles.

Remote Ubuntu mode:

- Require authentication before project access.
- Require HTTPS for non-local access.
- Recommend firewall rules allowing only SSH and HTTPS.
- Avoid exposing Docker socket to the web UI container.
- Restrict file browser access to the project volume.
- Add basic rate limits and upload size limits.
- Provide an update path for security fixes.

MVP authentication options:

- Simple admin password set during first-run setup.
- Reverse-proxy basic auth for early private deployments.
- Later: OAuth or passkeys if broader distribution requires it.

## MVP Scope

The first Docker Compose portal MVP should include:

- `docker compose up` deployment for local and Ubuntu remote use.
- First-run setup page.
- Admin password setup.
- Provider key setup and validation.
- New project wizard:
  - source file upload or server-side file selection
  - target language
  - stage preset
  - voice
  - custom instructions
  - optional subtitle file
- Local params JSON generation.
- Single-job queue.
- Worker launch through Docker.
- Stage progress display.
- Log tail display.
- Explicit failure display.
- Final artifact list and download.
- Support bundle export.
- Basic documentation for local and Ubuntu VM deployment.

## MVP Exclusions

- Multi-user roles.
- Hosted SaaS.
- Azure B2C login.
- Azure queue processing.
- Azure Blob as required storage.
- Autoscaling.
- Native desktop packaging.
- Local Whisper by default.
- DeepSeek as a full replacement provider.
- Agent-driven automatic repair.
- Public marketplace-style plugin system.

## Success Criteria

For a private alpha:

- A clean Ubuntu VM can run the portal from documented steps.
- A local Docker machine can run the portal from documented steps.
- A user can create a project without editing JSON by hand.
- Provider key validation catches missing/invalid keys before paid processing starts.
- One short MP3 can complete end to end and produce `voiceover.mp3`.
- Failed worker stages are visible in the UI with a useful log tail.
- Support bundle contains enough information to debug without leaking API keys.
- No secrets or personal deployment details are committed to the repo.

## Open-Source Readiness

Before making the repo public:

- Add a clear license.
- Add `README.md` with local and remote deployment instructions.
- Add `.env.example` with placeholder values only.
- Add `.gitignore` rules for `.env`, project volumes, logs, outputs, and generated artifacts.
- Add a secret scanning check.
- Remove personal paths from comments, docs, examples, and defaults.
- Remove Azure-specific deployment assumptions from the default path.
- Document which external services are required and which are optional.
- Document expected provider costs and limitations.
- Document security expectations for remote Ubuntu deployments.

## Recommended Repo Shape

Suggested structure under `/Users/sergey/Documents/github/podocracy-app`:

```text
podocracy-app/
  docker-compose.yml
  .env.example
  README.md
  prd/
    docker-compose-worker-portal-prd.md
  apps/
    web/
    app-api/
  worker/
    processing_container/
    common/
    Dockerfile
    requirements.txt
  docs/
    local-setup.md
    remote-ubuntu-setup.md
    security.md
```

This keeps the copied worker isolated from the new portal UI/API and makes it easier to review what came from the legacy repo.

## Recommended Next Steps

1. Decide whether the first alpha targets remote Ubuntu first, local Docker first, or both equally.
2. Create a minimal repo skeleton with Compose, `.env.example`, and docs.
3. Copy only the relevant worker files into `worker/` with no secrets, logs, generated artifacts, or personal paths.
4. Refactor the worker for local project folder mode.
5. Build a small `app-api` that can create params, launch one worker job, and collect logs.
6. Build a minimal web UI around project creation, progress, logs, and downloads.
7. Test one short MP3 on a clean Ubuntu VM.
8. Run secret/personal-path scans before any public release.

