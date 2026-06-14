# Gaps: Desktop Voiceover App PRD

Date: 2026-06-14
Companion to: `desktop-voiceover-app-prd.md` and `desktop-voiceover-app-prd-review.md`

Each gap lists: what the PRD assumes, the reality in code (`file:line`), impact, and a recommendation. Priority reflects MVP-blocking risk.

## P0 — Blocks MVP scope or success criteria as written

### G1. Status API has no stage-level failure detail
- **PRD assumes:** App can "not hide stage errors," show which stage failed, and run support checks on failure (MVP scope; Self-Healing section).
- **Reality:** `ProjectStatus` is only `{state, progress, updated_at}` (`apps/api/app/schemas/projects.py:7-10`); `_project_status_from_metadata` returns just those (`apps/api/app/api/v1/files.py:171-180`). `state` holds the current stage name or `failed`, but no error message, failed-stage list, or history.
- **Impact:** Desktop app cannot reliably surface or diagnose stage errors. Failed-stage detection is impossible from the API alone.
- **Recommendation:** Extend the status response with `current_stage`, `last_error`, and ideally a stage list/artifact manifest. Listed in PRD "Worker Improvements," but it is an MVP prerequisite, not a nice-to-have.

### G2. Worker stage failures are non-fatal but reported as `completed`
- **PRD assumes:** Failures are detectable and recoverable.
- **Reality:** `apps/api/.../voice-over-service/backend/processing_container/pd-00-orchestrator.py:298-311` logs a failed stage, does not break/raise, then unconditionally sends `state="completed", progress=100`.
- **Impact:** A partially-broken job looks successful. Users download incomplete output with no error signal. Directly undermines the success criterion "failures produce a support bundle sufficient to diagnose."
- **Recommendation:** Make stage failure mark the project `failed` (or `completed_with_errors`) with the failing stage recorded. PRD already calls for this; treat as a hard dependency for G1.

### G3. Worker log bundle cannot be downloaded via the API
- **PRD assumes:** Support bundle includes "downloadable worker logs if available"; support check "did worker logs contain known failure strings."
- **Reality:** Worker writes `<basename>.logs_<timestamp>.zip` (`pd-00-orchestrator.py:143-144`), but asset download is whitelisted to legacy patterns only — no `.zip`/logs (`apps/api/app/api/v1/files.py:407-416`).
- **Impact:** The maintainer-facing support bundle and the "scan logs" support check are not achievable in v1.
- **Recommendation:** Add a log-bundle download path (extend the whitelist or add a dedicated endpoint), or cut log-dependent features from v1 success criteria.

## P1 — Will cause incorrect implementation if not corrected

### G4. `advanced_options.*` is an untyped dict, not a schema
- **PRD assumes:** A stable list of 8 `advanced_options.*` fields (lines 62-68).
- **Reality:** `ProjectSettingsPayload.advanced_options` is `dict[str, Any]` (`apps/api/app/schemas/batch.py:32-40`); sub-fields read ad hoc in `files.py:610-636`; OpenAPI marks it `additionalProperties: true`.
- **Impact:** No compile-time/schema guarantee; field drift between clients is silent. A generated client will type this as an open dict.
- **Recommendation:** Promote `advanced_options` to a typed sub-model before/with the "publish API client schema" step. Until then, keep the web UI as source of truth (PRD line 71 is correct).

### G5. URL ingestion does not flow through `params/batch`
- **PRD assumes:** Scenario A treats upload+params uniformly.
- **Reality:** `source_url` items in `params/batch` are rejected at runtime (`files.py:838-847`, `mixed_input_not_supported`); URLs use `POST /v1/me/uploads/url` (`apps/api/app/api/v1/uploads.py:244`), as the web UI does (`upload-workspace.tsx:697-700`).
- **Impact:** A desktop client that mirrors "files or URL" through one params call will fail for URLs.
- **Recommendation:** Document the two distinct ingestion paths in the desktop `apiClient` design.

### G6. `yt-dlp` is invoked but not packaged in the worker image
- **PRD assumes:** URL ingestion works (Scenario A/B, BEMA URL upload).
- **Reality:** `pd-005-url-processing.py:7-16, 76-84` shells out to `yt-dlp`, but it is absent from `req.backend.txt` and `Dockerfile_backend` (only in `_dev/older-versions/`).
- **Impact:** YouTube URL ingestion breaks in a clean container build. Latent production bug, independent of the desktop app.
- **Recommendation:** Add `yt-dlp` to the worker image/requirements. PRD frames this as optional ("if URL ingestion is supported") — it is currently broken if URL is supported at all.

## P2 — Confirmed-missing future work (PRD already lists; recording for tracking)

### G7. No `/v1/me/healthcheck`
- Only root `/health` exists (`apps/api/app/main.py:46-49`); no per-user authenticated health endpoint. Needed for clean MVP support checks. (PRD "API Contract Work.")

### G8. No settings dry-run / validation endpoint
- No `/v1/me/settings/*` validation/dry-run route; only Pydantic-on-submit (`batch.py:50-72`). Needed for "validate before submit" UX. (PRD "API Contract Work.")

### G9. `params.json` optional with silent defaults
- Missing params falls back to `{"language": "RU", "stages_to_run": "all"}` (`shared_functions.py:472-480`). Minor, but a local-worker scenario should not rely on params being required.

### G10. macOS-only `caffeinate` calls are unguarded
- `caffeinate` invoked directly in `pd-010-raw-transcribe.py:204-205`, `pd-040-improve.py:21-22`, `shared_functions.py:916-917`; not installed anywhere and macOS-specific. Reinforces PRD's "local worker is high-risk" rating (Scenarios B/C).

## Recommended sequencing impact

- **Before locking MVP scope/success criteria:** resolve **G1, G2, G3** or explicitly cut the dependent features (stage error display, log-based support bundle/checks) from v1.
- **Before building the desktop `apiClient`:** account for **G4, G5**.
- **If URL ingestion is in v1:** fix **G6**.
- **Track G7–G10** as backend hygiene; they shape later scenarios but do not block the thin-wrapper MVP.
