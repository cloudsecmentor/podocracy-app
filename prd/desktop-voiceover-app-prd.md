# Podocracy Desktop Voiceover App PRD

Date: 2026-06-14

## Summary

Podocracy is currently running `podocracy.win` with the frontend and API from `/Users/sergey/Documents/azure-devops/podocracy-tech`. Voiceover processing is still performed by the legacy worker from `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container`.

This PRD evaluates a small desktop app for a handful of users on older macOS and Windows laptops. The app should collect the same per-file information as the current web upload flow, produce similar voiceover results, and help verify whether users can successfully work with OpenAI APIs or whether DeepSeek should be supported as a lower-cost or preferred model provider.

The lowest-risk first release is still a thin desktop wrapper around the existing `/v1/me/*` API contract, plus a guided manual-recording/export path inspired by the BEMA workflow. However, if the product decision is to stop hosting the API/workers in Azure and instead give users the worker to run locally with their own provider keys, the MVP changes substantially: the app becomes a local pipeline manager, not an upload client.

Converting the current worker into an end-user app is medium-high difficulty for a Docker-based wrapper and high difficulty for a polished native app. The worker can run locally in principle, but today it is infrastructure code with Linux, Azure, subprocess, env-var, and API-callback assumptions.

## Codebase Findings

### Current Podocracy Web/API Flow

The current web upload experience is already organized around API calls that a desktop app can reuse.

Relevant source:

- `/Users/sergey/Documents/azure-devops/podocracy-tech/apps/web/src/app/upload/upload-workspace.tsx`
- `/Users/sergey/Documents/azure-devops/podocracy-tech/apps/api/app/api/v1/files.py`
- `/Users/sergey/Documents/azure-devops/podocracy-tech/apps/api/app/api/v1/uploads.py`
- `/Users/sergey/Documents/azure-devops/podocracy-tech/apps/api/app/schemas/batch.py`
- `/Users/sergey/Documents/azure-devops/podocracy-tech/apps/api/PROCESSING.md`

The current sequence is:

1. User authenticates through Azure AD B2C.
2. Web UI uploads one or more files through `/v1/me/uploads/batch`.
3. Web UI writes one params file per source through `/v1/me/projects/params/batch`.
4. If `start_processing=true`, the API queues a processing message.
5. Internal dispatch provisions the worker VM/container.
6. The legacy worker reads the source file and `<basename>.params.json`, runs processing stages, writes artifacts, and reports status.
7. Web UI lists projects and downloads assets through `/v1/me/projects/display`, `/v1/me/projects/status`, and `/v1/me/assets/download`.

Important desktop-facing endpoints:

- `GET /v1/me/profile`
- `GET /v1/me/settings/visibility`
- `POST /v1/me/uploads/batch`
- `POST /v1/me/uploads/direct/prepare`
- `POST /v1/me/uploads/direct/complete`
- `POST /v1/me/uploads/url`
- `POST /v1/me/uploads/bema`
- `POST /v1/me/projects/params/batch`
- `GET /v1/me/projects/display`
- `GET /v1/me/projects/status`
- `GET /v1/me/assets/download`
- `GET /v1/me/assets/stream`
- `GET/PUT /v1/me/assets/edit`

The settings payload needed by a desktop app is already close to a stable contract:

- `display_name`
- `language`
- `stages`
- `stages_ui`
- `voice`
- `custom_instructions`
- `custom_subtitles`
- `advanced_options.whisper_api`
- `advanced_options.whisper_model`
- `advanced_options.max_char_chunk`
- `advanced_options.voiceover_tempo`
- `advanced_options.voiceover_shift`
- `advanced_options.custom_recording`
- `advanced_options.autogenerate_custom_instructions`
- `advanced_options.use_subtitles_as_is`

The desktop app should treat the web UI as the source of truth for field names and defaults unless the API schema is formalized into a shared client package.

### Authentication and Identity

The current API expects a user bearer token from Azure AD B2C for `/v1/me/*` routes. User blob containers are resolved from canonical token identity, mainly `oid` or `sub`, not from a local machine account.

Desktop implication:

- A desktop app should use the same Azure B2C OAuth flow and attach the bearer token to API requests.
- For older users, a device-code or browser-based sign-in flow is likely easier than embedding a custom auth UI.
- Avoid giving the desktop app internal API keys, Azure storage connection strings, or worker credentials in the MVP.

### Legacy Worker Pipeline

Relevant source:

- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-00-orchestrator.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/shared_functions.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/parameters.json`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-005-preprocess.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-010-raw-transcribe.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-030-translate.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-040-improve.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-050-voiceover.py`
- `/Users/sergey/Documents/azure-devops/voice-over-service/backend/processing_container/pd-055-postprocess.py`

The worker is a sequential Python subprocess pipeline. It expects a source path and a matching `<basename>.params.json`, then runs stages such as:

- `preprocess`
- `url`
- `subtitles`
- `transcribe`
- `combine`
- `timesync`
- `translate`
- `customize`
- `improve`
- `voiceover`
- `postprocess`

Outputs are named from the source basename:

- `<basename>.raw.json`
- `<basename>.combined.json`
- `<basename>.translated.json`
- `<basename>.improved.json`
- `<basename>.voiceover.mp3`
- `<basename>.voiceover.mp4`
- `<basename>.logs_<timestamp>.zip`

Important constraints:

- The worker is Linux-first. It installs or assumes `ffmpeg`, `bc`, Python packages, Azure blob access, and API callback credentials.
- It assumes network access for OpenAI, DeepL, optional ElevenLabs, Azure Blob Storage, and the Podocracy API.
- The current orchestrator can continue after a failed stage and still mark work as completed. A desktop app should not hide stage errors.
- Local native execution on old Windows/macOS machines is likely fragile. Docker is more realistic, but Docker may be too heavy for some old laptops.

### Local Worker Conversion Assessment

If users run the worker locally and provide their own API keys, the hosted Podocracy API stops being the center of the workflow. The app must replace several cloud responsibilities that are currently hidden from users.

Required conversion work:

- Define a first-class local project folder contract: source file, params JSON, optional subtitles, intermediate JSON, final audio/video, and logs.
- Generate `params.json` locally from the same settings users provide in the web app.
- Make Azure Blob Storage optional or remove it from the local path; local files should be the default source of truth.
- Make API status callbacks optional; the app should read progress from stdout, a local status file, or a small local worker API.
- Make stage subprocess failures fatal or mark the project `failed`/`completed_with_errors`; current behavior can hide broken stages.
- Store user provider keys securely in the OS keychain or equivalent, not plain `.env` files.
- Add onboarding checks for `OPENAI_API_KEY`, `DEEPL_AUTH_KEY`, optional `ELEVENLABS_API_KEY`, and optional DeepSeek configuration.
- Package or detect `ffmpeg`, Python dependencies, and optionally `yt-dlp`.
- Disable local Whisper by default on old laptops; keep `whisper_api=true` unless a user explicitly chooses local transcription.
- Remove or guard Linux/cloud assumptions such as runtime `apt-get`, Azure CLI managed identity secret retrieval, and macOS-only helper calls.

Difficulty estimate:

- **Docker wrapper:** medium-high. Fastest path because the worker already has a Linux container shape, but users must be able to install/run Docker and the app must manage volumes, env vars, logs, and updates.
- **Native bundled app:** high. Better user experience if done well, but requires refactoring scripts into a portable runtime and bundling Python, `ffmpeg`, audio libraries, and model/provider configuration across macOS and Windows.
- **Agent/skills wrapper only:** high support risk for normal users. Useful as maintainer tooling, but not a replacement for deterministic packaging and progress/error handling.

### Model Provider Findings

Current model/provider usage is not abstracted.

OpenAI is used for:

- Whisper API transcription
- Timesync/speaker sync
- Custom instruction generation
- Text improvement
- TTS through `gpt-4o-mini-tts`

DeepL is used for translation.

ElevenLabs is optionally supported for TTS.

DeepSeek is not currently integrated. A DeepSeek option would require a provider abstraction around chat-completion style LLM calls first. It might be useful for `customize`, `timesync`, and `improve`, but it does not replace:

- OpenAI Whisper API unless another transcription provider is added.
- OpenAI TTS unless another TTS provider is added.
- DeepL translation unless translation is moved into an LLM stage.

DeepSeek should therefore be treated as a partial provider option, not as a drop-in replacement for the whole pipeline.

### BEMA Manual Recording Flow

Relevant source:

- `/Users/sergey/Documents/BEMA_az/bema/scripts/aws-03-man-trunc-silence.py`
- `/Users/sergey/Documents/BEMA_az/bema/scripts/aws-04-combine.py`
- `/Users/sergey/Documents/BEMA_az/bema/scripts/aws-05-tags.py`
- `/Users/sergey/Documents/BEMA_az/bema/scripts/aws-06-mp3chapsJsonCsv.py`
- `/Users/sergey/Documents/BEMA_az/bema/scripts/aws-07-apply-chapters.py`
- `/Users/sergey/Documents/BEMA_az/bema/scripts/shared_clicks_removal.py`

The BEMA process shows a useful manual alternative:

1. User records while reading prepared text in Audacity.
2. User exports labeled segment files.
3. `aws-03` trims silence and optionally changes tempo.
4. `aws-04` aligns chunks back to the original episode timeline and ducks original audio underneath.
5. `aws-05`/`aws-06`/`aws-07` add tags, descriptions, chapters, and final export metadata.

This is valuable for a desktop MVP because it separates "create good speech audio" from "run the entire AI pipeline locally." It also shows that a small guided app can improve user success by handling naming, silence cleanup, preview, and export while leaving recording itself to Audacity or a simple built-in recorder.

## User Problem

Users need a simple way to provide the same file-level information as the Podocracy web app and receive equivalent voiceover outputs, without needing to understand Azure, Docker, worker logs, params JSON files, or command-line scripts.

The current users are few, known, and likely not technical. Some have older macOS or Windows laptops. The app should optimize for:

- Few required decisions.
- Clear progress and error messages.
- Recoverable failed uploads or jobs.
- Minimal local installation complexity.
- Ability to verify whether OpenAI API usage works for them before investing in a more complex local/offline tool.

## Goals

- Provide a desktop-friendly flow that mirrors the current web upload form.
- Reuse existing backend contracts where practical.
- Support source file upload, settings capture, job start, progress polling, and result download.
- Provide a BEMA-inspired manual recording path for users who prefer to record a human voiceover in Audacity.
- Capture evidence on whether users can successfully use OpenAI APIs and whether DeepSeek should be introduced.
- Keep the first shippable version supportable by one maintainer.

## Non-Goals

- Full offline processing in the MVP.
- Shipping Azure storage keys or internal API keys to end-user machines.
- Rewriting the legacy worker before validating user demand.
- Replacing the web app.
- Replacing DeepL translation, Whisper transcription, and TTS providers in one step.
- Building a fully self-healing autonomous agent system in the MVP.

## Design Scenarios

### Scenario A: Thin Desktop Wrapper Around Podocracy API

The desktop app mirrors the web flow and delegates processing to the existing cloud API and worker.

Flow:

1. User signs in through Azure B2C.
2. User selects source file or URL.
3. App shows the same fields as web: language, stage preset, voice, custom instructions, subtitles, advanced options.
4. App uploads through `/v1/me/uploads/batch` or direct upload.
5. App creates params through `/v1/me/projects/params/batch`.
6. App starts processing in the cloud.
7. App polls project status.
8. App downloads `voiceover.mp3`, `voiceover.mp4`, logs, and JSON artifacts when available.

Pros:

- Reuses the existing production API and worker.
- Lowest implementation risk.
- No local Docker, Python, ffmpeg, or API key setup for users.
- Easier to support older laptops.
- Can be shipped as a simple Tauri, Electron, Flutter, or Python GUI app.
- Keeps billing, identity, and storage consistent with the web app.

Cons:

- Requires reliable internet.
- Requires current cloud worker reliability.
- Users still depend on OpenAI/DeepL through the server-side worker.
- Does not prove that users can run OpenAI APIs directly from their machine.
- Not meaningfully different from the web app unless it adds simpler UX, resumable uploads, or local helpers.

Best use:

- First MVP for real users.

### Scenario B: Desktop App Plus Local Docker Worker

The app gives users the worker as a local Docker runtime. Files, params, logs, and outputs live in a local project folder. Users provide provider API keys.

Flow:

1. App creates a local project folder.
2. User selects source file, language, stages, voice, subtitles, and custom instructions.
3. App runs worker container locally.
4. Worker reads local files and local params.
5. Worker calls OpenAI, DeepL, optional DeepSeek, and optional ElevenLabs directly using user-provided keys.
6. App surfaces logs and stage failures.
7. App exports final artifacts from the local project folder.

Pros:

- Removes hosted worker/API cost and Azure worker operations.
- Enables experimentation with user-owned API keys.
- Can run long jobs without provisioning an Azure VM.
- Closer to a "local app" mental model.
- Keeps the existing Linux worker environment mostly intact.

Cons:

- Docker may not run well on old laptops.
- Windows/macOS packaging and ffmpeg/audio dependencies become support burdens.
- Worker currently assumes Azure Blob, API callbacks, cloud credentials, and cloud-style status.
- Users now need to manage paid provider keys and provider billing.
- Existing worker failure reporting is not robust enough for nontechnical users.
- Local CPU/RAM may be weak, especially if local Whisper is enabled.
- Larger app support surface: Docker install, image updates, volume mounts, env vars, logs, and provider account setup.

Best use:

- Best first path if the strategic goal is to stop hosting Azure processing while avoiding a full native rewrite.

### Scenario C: Native Local Pipeline Without Docker

The desktop app bundles or installs Python, ffmpeg, and the worker scripts directly.

Pros:

- Avoids Docker installation.
- Can be integrated more tightly with native file pickers, progress UI, and local folders.
- Potentially smaller than Docker if carefully packaged.

Cons:

- Highest support risk across old macOS and Windows.
- Native Python package drift, ffmpeg path issues, shell assumptions, `apt-get`, and macOS-specific `caffeinate` behavior all need cleanup.
- Hard to keep consistent with the production worker.
- Security and secret storage become a product problem.

Best use:

- Not recommended until the worker is refactored into a portable library with provider abstractions and clean stage exit behavior.

### Scenario D: Manual Recording Assistant Inspired by BEMA

The app helps users create human-recorded voiceover assets. Audacity can remain the recorder at first, while the app handles prompts, naming, silence cleanup, chapter metadata, and final export.

Flow:

1. App imports source transcript/chunks or a Podocracy `combined.json`/`improved.json`.
2. App displays each segment for the user to read.
3. User records in Audacity or in a simple app recorder.
4. App validates exported segment names and durations.
5. App trims silence and optionally removes clicks.
6. App aligns segments to the original episode or concatenates voiceover-only audio.
7. App applies tags and chapters.
8. App exports final MP3 and optionally uploads it to Podocracy.

Pros:

- Works even when AI TTS quality or cost is a concern.
- Builds on a proven BEMA-style workflow.
- Avoids requiring every step of the AI pipeline locally.
- Gives users a clear fallback when model APIs fail.
- Can produce high-quality human narration with modest automation.

Cons:

- Still requires user effort and discipline.
- Audacity adds an external tool unless recording is built in.
- Segment timing and naming must be made much easier than the current script workflow.
- Silence trimming dependencies still need packaging.

Best use:

- Strong MVP companion feature, especially for users who already know Audacity or prefer manual narration.

### Scenario E: Agent/Skills Wrapper Around OpenCode or Another Harness

Instead of a precompiled app with all logic embedded, ship a small wrapper plus a set of skills/prompts/scripts that an agent harness can execute. The agent can inspect logs, retry failed steps, edit params, switch models, and guide the user.

Pros:

- Flexible and fast to evolve.
- Can encode operational knowledge as skills instead of hard-coded UI.
- May help "issues fix themselves" by detecting known failures and applying known remedies.
- Useful for maintainer workflows and advanced users.
- Can bridge legacy scripts while the product is still changing.

Cons:

- Risky for nontechnical users.
- Agent harness availability, API keys, permissions, and safety need careful design.
- Self-healing is only reliable for known, bounded failure modes.
- Debugging agent behavior may be harder than debugging a deterministic app.
- Shipping skills does not eliminate the need for a stable runtime, logs, secrets, and rollback.

Best use:

- Maintainer/admin mode, not the first end-user MVP.
- A support tool that can package logs, explain failures, and suggest fixes.

## Recommended MVP

There are two different MVPs depending on the hosting decision. They should not be mixed casually.

If Podocracy continues hosting API and workers, ship the thin desktop wrapper plus an optional manual-recording assistant. This remains the lowest-risk MVP for older laptops.

If Podocracy stops hosting API/workers and gives users the worker, ship a local-worker MVP instead. In that case, the MVP is no longer about upload, queue, and download; it is about local project management, provider-key setup, local runtime packaging, progress/error handling, and local artifact export.

### Cloud Wrapper MVP Scope

The cloud-wrapper MVP should include:

- Sign in with Podocracy account through browser/device-code OAuth.
- Upload local media file or submit URL.
- Show the same basic settings as the web app:
  - project name
  - target language
  - preset stages
  - voice
  - custom instructions
  - subtitle file
  - start now vs save only
- Hide advanced settings by default, but allow a support/debug panel:
  - whisper API vs local model
  - max char chunk
  - voiceover tempo
  - voiceover shift
  - custom recording
  - use subtitles as-is
- Start cloud processing using existing API.
- Poll and show status.
- Download and open result folder.
- Export a support bundle containing:
  - selected settings
  - project id
  - API status history
  - downloadable worker logs if available
  - app logs
- Include a manual recording mode:
  - import/display chunks from `improved.json` or a simple text file
  - guide Audacity export naming, or provide a minimal built-in recorder later
  - validate segment filenames
  - optionally run silence cleanup if ffmpeg is available
  - export/upload final voiceover asset when ready

### Local Worker MVP Scope

The local-worker MVP should include:

- Create a local project folder per source file.
- Copy or reference the source media file locally.
- Show the same basic settings as the web app:
  - project name
  - target language
  - preset stages
  - voice
  - custom instructions
  - subtitle file
- Generate a local params JSON file from those settings.
- Store provider keys securely:
  - OpenAI for Whisper/TTS and current text stages
  - DeepL for translation
  - optional ElevenLabs for TTS
  - optional DeepSeek for text stages after provider abstraction exists
- Run provider validation before the first paid job:
  - key present
  - small test request succeeds
  - selected model/provider is supported for the selected stage
- Launch the worker locally, preferably through Docker for the first technical alpha.
- Stream or poll local progress.
- Stop on stage failure and show the failing stage/log tail.
- Export local artifacts:
  - `voiceover.mp3`
  - `voiceover.mp4` when video postprocess is applicable
  - `raw.json`, `combined.json`, `translated.json`, `improved.json` when produced
  - logs and support bundle
- Include a manual recording fallback:
  - import/display chunks from `improved.json` or text
  - guide Audacity export naming
  - validate segment files
  - optionally run silence cleanup if `ffmpeg` is available
  - export a final local MP3

### Cloud Wrapper MVP Exclusions

Do not include these in the cloud-wrapper first release:

- Local Docker worker execution.
- Native Python worker packaging.
- User-managed Azure credentials.
- Fully autonomous self-healing agent actions.
- Full DeepSeek replacement of all AI stages.
- Complex chapter artwork and publishing sidecars.

### Local Worker MVP Exclusions

Do not include these in the local-worker first release:

- Azure B2C login.
- `/v1/me/uploads/*` upload flow.
- Azure queue dispatch.
- Azure Blob Storage as the required project store.
- User-managed Azure storage credentials.
- Local Whisper by default on old laptops.
- Native packaging before Docker-wrapper validation, unless Docker is impossible for the target machines.
- Full DeepSeek replacement of transcription, translation, and TTS.
- Autonomous agent repair without explicit user approval.

### Cloud Wrapper MVP Success Criteria

For the first 3 to 5 users:

- At least 80 percent can sign in without help.
- At least 80 percent can upload a file and start a job.
- At least 80 percent can find and download the final voiceover.
- Failures produce a support bundle sufficient for the maintainer to diagnose the issue.
- Users can complete one short test project using the default OpenAI-backed server pipeline.
- At least one user can complete the manual recording path with Audacity-exported files.

### Local Worker MVP Success Criteria

For the first 3 to 5 users:

- At least 80 percent can install and launch the app.
- At least 80 percent can configure required provider keys with guided checks.
- At least 80 percent can create a local project and run one short file end to end.
- The app produces a final local `voiceover.mp3` for a short file.
- The app clearly reports provider-key failures, missing `ffmpeg`, Docker/runtime failures, and stage failures.
- Failures produce a local support bundle with params, app logs, worker logs, stage logs, and environment checks.
- Users can understand provider cost exposure before starting a paid run.

## OpenAI vs DeepSeek Validation Plan

The first MVP should answer a practical question: are user problems caused by provider cost/access, output quality, app UX, or local machine constraints?

### Phase 1A: Server-Side OpenAI Baseline

Use the existing server-side OpenAI/DeepL/TTS pipeline. Users do not supply model API keys.

Measure:

- Upload success.
- Queue/start success.
- End-to-end completion time.
- Cost per minute or per project.
- Quality feedback on translation, improvement, and voiceover.
- Number of support interventions.

Decision:

- If most users succeed and cost is acceptable, keep OpenAI as default for MVP.
- If cost is the main issue, add provider comparison server-side before asking users to manage keys.
- If quality is the issue, compare DeepSeek only on text stages first.
- If upload/auth/progress is the issue, changing models will not help.

### Phase 1B: Local User-Key Baseline

If the MVP moves to local worker distribution, replace the server-side baseline with a local user-key baseline.

Use the current provider mix first:

- OpenAI Whisper API for transcription.
- DeepL for translation.
- OpenAI text models for `customize`, `timesync`, and `improve`.
- OpenAI TTS or ElevenLabs for voiceover.

Measure:

- Key setup success.
- Provider test-call success.
- Local runtime startup success.
- End-to-end completion time.
- Provider errors and rate limits.
- Estimated and actual provider cost per project.
- Quality feedback on translation, improvement, and voiceover.

Decision:

- If users cannot configure keys reliably, local-worker distribution is not ready for nontechnical users.
- If provider cost is the issue, compare DeepSeek for text stages before changing transcription or TTS.
- If runtime startup is the issue, prioritize packaging and diagnostics over model changes.

### Phase 2: Text-Stage Provider Experiment

Add a server-side provider setting for chat-completion stages only:

- `improve`
- `customize`
- possibly `timesync`

Keep these unchanged initially:

- DeepL translation
- OpenAI Whisper API
- OpenAI or ElevenLabs TTS

Implementation direction:

- Create a small LLM provider adapter in the worker.
- Keep one internal request/response shape for chat stages.
- Support OpenAI-compatible providers by `base_url`, `api_key`, and `model`.
- Add project params such as `llm_provider`, `llm_model`, and optionally `llm_base_url`.
- Store provider choice in params and logs.
- Never expose arbitrary provider base URLs to normal users until security implications are reviewed.

Measure:

- Improvement quality vs OpenAI.
- JSON/output format reliability.
- Retry rate.
- Cost.
- Latency.
- Failure messages.

Decision:

- If DeepSeek is cheaper and reliable for improve/customize, offer it as an advanced or account-level option.
- If DeepSeek breaks formatting often, keep it internal until prompts and validators are improved.

### Phase 3: User-Owned API Keys

Only test user-owned keys after the app has proven the basic cloud flow, unless the product decision is explicitly local-worker-first.

Options:

- Store keys server-side per user with encryption and run jobs in cloud.
- Store keys locally in OS keychain and run local worker or proxy requests through app.

Recommendation:

- For the cloud-wrapper MVP, prefer server-side encrypted keys if user-owned keys become necessary.
- For the local-worker MVP, store keys locally in the OS keychain and never write them into project folders, support bundles, logs, or params JSON.

## Self-Healing and Agent-Assisted Support

"Issues fix themselves" is a good direction, but should start as deterministic health checks before autonomous repair.

### MVP Support Automation

Add deterministic checks:

- Can reach API.
- Can authenticate.
- Can upload a small test file.
- Can poll project status.
- Can download an existing asset.
- Is ffmpeg installed if manual recording cleanup is enabled.
- Are expected artifacts present.
- Did worker logs contain known failure strings.

For the local-worker MVP, replace API checks with local runtime checks:

- Can create a project folder.
- Can read/write params and output files.
- Can locate Docker or the bundled runtime.
- Can locate `ffmpeg`.
- Can run a worker smoke test.
- Can validate provider keys.
- Can detect missing or empty output artifacts.
- Can collect local app and worker logs.

For known failures, show a specific recovery action:

- Re-authenticate.
- Retry upload.
- Save params only and start later.
- Use smaller file.
- Disable timesync if proofread file is missing.
- Use cloud processing instead of local worker.
- Install or select ffmpeg.
- Re-enter or replace provider key.
- Switch from local runtime to Docker runtime, or from Docker runtime to manual recording, when available.

### Later Agent Mode

An OpenCode or similar harness wrapper could be useful for maintainer mode:

- Read support bundle.
- Summarize failure.
- Suggest or apply params changes.
- Retry failed stages.
- Compare OpenAI vs DeepSeek outputs.
- Prepare a bug report.

Guardrails needed:

- Read-only by default.
- Explicit user approval before modifying local files or starting paid API calls.
- No access to internal API keys on end-user machines.
- Fixed skill set for known workflows.
- Full transcript/log export for maintainer review.

Recommendation:

- Ship deterministic support checks in MVP.
- Prototype agent-assisted repair as a separate maintainer tool.
- Do not make agent self-healing the core promise for the first user release.

## Technical Shape

### Suggested App Architecture

Use a simple cross-platform desktop shell:

- Tauri if smaller binaries and web UI reuse are important.
- Electron if fastest reuse of existing React patterns is important.
- Python GUI only if the main priority is wrapping existing Python scripts and not reusing web UI.

For this user base, Tauri or Electron are more attractive than native Python because:

- The current product UI is already web/React-oriented.
- OAuth browser handoff is natural.
- Packaging can include a small local service later if needed.
- The desktop app can stay mostly API-driven.

For a local-worker MVP, Tauri or Electron can still be the shell, but the core product is the runtime manager. The app needs a local worker adapter that can create params, launch the worker, stream progress, collect logs, and manage provider keys.

Suggested modules:

- `auth`: browser/device-code sign-in and token refresh.
- `apiClient`: typed wrappers for `/v1/me/*`.
- `projectWizard`: file selection and settings capture.
- `uploadManager`: resumable/direct uploads and retries.
- `statusMonitor`: polling and status history.
- `downloadManager`: result download/open folder.
- `manualRecording`: chunk display, segment validation, optional ffmpeg cleanup.
- `supportBundle`: logs, settings, status history, known failure checks.
- `providerExperiment`: hidden/internal model-provider fields for controlled testing.

Additional modules for local-worker MVP:

- `localProject`: project folder creation, file layout, artifact discovery.
- `keyManager`: OS keychain storage and provider validation.
- `runtimeManager`: Docker/native runtime detection, launch, update, and diagnostics.
- `workerProgress`: parse stdout/status files and expose stage progress.
- `stageLogs`: collect per-stage logs and failure tails.
- `costPreview`: explain provider usage before starting paid work.

### API Contract Work Before App Build

Before implementing the desktop app, consider tightening:

- Publish an API client schema generated from OpenAPI.
- Ensure `/v1/me/projects/params/batch` remains the single source for params creation.
- Add a clear status endpoint response for stage-level failures.
- Add a way to download the latest worker log bundle from the project display or assets API.
- Add a dry-run or validation endpoint for settings payloads.
- Add a small `/v1/me/healthcheck` endpoint if current endpoints do not cover support checks cleanly.

### Worker Improvements That Help Any Scenario

These are useful regardless of desktop design:

- Make stage subprocess failures fatal or explicitly mark project `failed`.
- Add stage-level status with current stage, last error, and artifact list.
- Add provider abstraction for chat-completion stages.
- Add output validators for LLM-generated JSON/text.
- Add `yt-dlp` explicitly to the worker image if URL ingestion is supported.
- Document exact params contract consumed by the worker.
- Keep default path cloud-first and avoid local machine dependency in user MVP.

For the local-worker MVP, add:

- A `--local-mode` or equivalent runtime mode that disables Azure/API assumptions.
- A local params schema that can be generated by the app without the hosted API.
- A local progress/status file or structured stdout events.
- A local artifact manifest written at the end of each stage.
- A safe secret-loading path that never persists API keys into artifacts.
- A Docker image update/version check visible to the app.

## Risks

- Users may expect the desktop app to work fully offline. The cloud-wrapper MVP should clearly state that processing is cloud-backed; the local-worker MVP should clearly state that model providers still require internet access and paid API keys.
- Old laptops may struggle with large file upload, local audio preview, and any bundled processing.
- DeepSeek may reduce cost for text stages but cannot replace Whisper/TTS/DeepL without more provider work.
- Local worker support can quickly become a second product.
- Agent-based self-healing can create trust and safety problems if it starts paid jobs or edits files without clear approval.
- In the local-worker MVP, users are directly exposed to provider billing, rate limits, account setup, and key security.
- Docker-based local processing may be impossible on some old machines; native packaging may be expensive to support.
- Local support bundles must be scrubbed to avoid leaking API keys or source content the user does not want to share.

## Open Questions

- Are users expected to bring their own OpenAI or DeepSeek keys, or should Podocracy continue owning provider credentials?
- Is the desktop app primarily for easier UX, local/manual recording, or cost reduction?
- Should manual recordings be uploaded into the existing project artifacts, or exported as a separate local deliverable?
- Is BEMA-style chapter/tag export needed for Podocracy users in the first release?
- Which old macOS versions and Windows versions must be supported?
- Should the MVP be installed app only, or can it start as a packaged local web app/PWA plus helper scripts?
- Can the target users install and run Docker, or must the worker be bundled natively?
- If users provide their own keys, who supports provider account creation, billing surprises, rate limits, and quota errors?

## Recommended Next Steps

1. Decide between cloud-wrapper MVP and local-worker MVP before building the app shell.
2. If cloud-wrapper MVP: build a clickable prototype that mirrors the web upload wizard and downloads existing project results.
3. If local-worker MVP: define the local project folder contract and params schema first.
4. If local-worker MVP: create a Docker-wrapper proof of concept that runs one short local file with user-provided OpenAI and DeepL keys.
5. Add support bundle export and known failure checks for the chosen MVP path.
6. Add manual recording import/validation using BEMA naming concepts.
7. Prototype DeepSeek only for `improve` and `customize` after a provider adapter exists.
8. Treat agent-assisted repair as a separate maintainer/admin track.

