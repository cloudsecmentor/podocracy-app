# Review: Desktop Voiceover App PRD

Reviewer: AI coding agent
Date: 2026-06-14
Subject: `desktop-voiceover-app-prd.md`
Method: Claim-by-claim validation against the three referenced repos (`podocracy-tech`, `voice-over-service`, `BEMA_az/bema`), with `file:line` evidence.

## Verdict

The PRD is well-structured, honest about risk, and its central recommendation (thin API wrapper + manual-recording assistant as MVP; defer local worker, agent self-healing, and full DeepSeek) is sound and well-supported by the code.

Most factual claims check out. The corrections below matter because several PRD sections quietly assume capabilities that **do not yet exist** in the API or worker (status detail, log-bundle download, healthcheck, URL via params, packaged `yt-dlp`). Those assumptions are scattered across the MVP scope, success criteria, and support-automation sections, so they need to be surfaced as explicit prerequisites rather than left implicit.

This review pairs with `desktop-voiceover-app-prd-gaps.md`, which lists the gaps as actionable items.

## What the PRD gets right (validated)

- **`/v1/me/*` endpoint list is accurate.** All endpoints in the PRD are registered (`apps/api/app/api/v1/uploads.py`, `apps/api/app/api/v1/files.py`), and confirmed in `docs/api/openapi.json`.
- **Auth model is correct.** `/v1/me/*` routes depend on `get_current_user_claims`; bearer JWT is validated against Azure AD B2C JWKS (`security.py:42-176`); the blob container is the canonical token id (`oid` then `sub`) via `identity.py:22-28` and `user_projects_service.py:17-41`. The PRD's "do not ship storage keys / resolve from token identity" guidance is correct.
- **Processing sequence + params contract are accurate.** `PROCESSING.md:21-37` and `schemas/batch.py:43-72` match the implementation (`files.py:962-991`). The web UI uses `/v1/me/uploads/batch` then `/v1/me/projects/params/batch` with `start_processing`, exactly as described (`upload-workspace.tsx:776-779`, `:984-996`).
- **Worker stages and artifact names are accurate.** Stage list and `<basename>.*` output naming match `pd-00-orchestrator.py:243-251` and `shared_functions.py:299-322`.
- **Provider usage is correctly characterized.** OpenAI (Whisper API, timesync speaker ID, customize, improve, `gpt-4o-mini-tts`), DeepL (translate), ElevenLabs (optional TTS) are all hardcoded with no abstraction; DeepSeek appears nowhere in the repo. The PRD's "DeepSeek is a partial provider option, not a drop-in replacement" conclusion is correct.
- **Stage failures are non-fatal — confirmed and important.** `pd-00-orchestrator.py:298-311` logs a failed stage but never breaks/raises, then unconditionally sends `state="completed", progress=100`. The PRD's "a desktop app should not hide stage errors" warning is well-founded.
- **BEMA flow is accurate.** Audacity record/export → `aws-03` (silence/click trim + optional tempo) → `aws-04` (timeline align + duck original) → `aws-05`/`aws-06`/`aws-07` (tags, chapter CSV/description, ID3 CHAP frames). Validated against the scripts and `bema/README.md:52-58`.

## Corrections (claims that are partially wrong or misleading)

1. **Settings `advanced_options.*` fields are NOT a typed contract.** `ProjectSettingsPayload` (`schemas/batch.py:32-40`) types only the top-level fields; `advanced_options` is an untyped `dict[str, Any]` (`additionalProperties: true` in OpenAPI). The eight sub-fields are read opportunistically in `files.py:610-636`. The PRD lists them as if they were a stable schema — they are a convention, not an enforced contract. This strengthens the PRD's own caveat at line 71 ("treat web UI as source of truth"), and it is a real argument for the "publish a schema" step.

2. **`source_url` in `params/batch` is rejected at runtime.** Although `ParamsBatchInputItem` allows `source_url` (`batch.py:43-49`), the handler rejects URL items with `mixed_input_not_supported` (`files.py:838-847`). URL ingestion goes through `POST /v1/me/uploads/url`. The PRD's Scenario A step 4 ("uploads through `/v1/me/uploads/batch` or direct upload") is fine, but any reader assuming URLs flow through `params/batch` would be wrong.

3. **`params.json` is optional to the worker, not required.** The PRD says the worker "expects ... a matching `<basename>.params.json`." In practice a missing params file falls back to defaults `{"language": "RU", "stages_to_run": "all"}` (`shared_functions.py:472-480`). When present, the filename must match (`pd-00-orchestrator.py:218-221`). Minor, but it affects how a local-worker scenario reasons about correctness.

4. **`yt-dlp` is invoked but not packaged.** URL ingestion calls the `yt-dlp` CLI (`pd-005-url-processing.py:7-16, 76-84`), but `yt-dlp` is absent from `req.backend.txt` and `Dockerfile_backend` (only present in `_dev/older-versions/`). The PRD's worker-improvements line "Add `yt-dlp` explicitly to the worker image if URL ingestion is supported" is correct — but it should be framed as **fixing a current latent break**, not an optional enhancement.

## Assumptions in the PRD that the backend does NOT currently support

These are the highest-value findings: several MVP features are written as if the backend already supports them. It does not. Each becomes a backend prerequisite or an MVP scope cut.

- **Stage-level failure detail.** PRD MVP/support sections assume the app can show stage errors and "did worker logs contain known failure strings." The status model is only `{state, progress, updated_at}` (`schemas/projects.py:7-10`); `state` carries the current stage name or `failed`, but there is no per-stage error, message, or history (`files.py:171-180`). Combined with non-fatal stage failures (#stage above), a project that hit an error stage can still report `completed`. The desktop app cannot reliably "not hide stage errors" against today's API.
- **Worker log bundle download.** PRD MVP success criteria and the support bundle both depend on "downloadable worker logs if available." Asset download is whitelisted to legacy patterns (`.url`, `transcript.txt`, `proofread.txt`, `improved.json`, `.voiceover.*`) in `files.py:407-416`; there is no path for `.logs_<timestamp>.zip`. The worker produces the zip, but the API will not serve it.
- **`/v1/me/healthcheck`.** PRD "API Contract Work" suggests adding it "if current endpoints do not cover support checks cleanly." Confirmed: only a root `/health` exists (`main.py:46-49`); there is no per-user authenticated health endpoint.
- **Settings dry-run / validation endpoint.** Does not exist; only Pydantic validation on submit. The PRD lists it as future work — flagging it here so it is treated as a real dependency for the "validate before submit" UX.

## Section-level notes

- **Summary / Recommended MVP:** Solid. The phased "wrapper first, then text-stage provider experiment, then user keys" ordering is the right call and matches the code's lack of provider abstraction.
- **Scenario B/C (local worker):** The PRD correctly rates these as high-risk. The `caffeinate` calls (`pd-010`, `pd-040`, `shared_functions.py:916-917`) are macOS-only and unguarded, and the Azure CLI login + blob I/O + API callbacks are deeply wired in. "Local worker can become a second product" is accurate.
- **Self-Healing / Support Automation:** The deterministic-checks-first approach is right, but the MVP check "did worker logs contain known failure strings" is not achievable until log-bundle download exists (see gaps). Re-scope it to API-reachability/auth/upload/poll/download checks for v1.
- **Success Criteria:** "Failures produce a support bundle sufficient to diagnose" is partly blocked by missing log access and thin status. Either narrow the criterion or add the backend work as a prerequisite.
- **Open Questions:** Good and genuinely open. Suggest adding two: (a) Will the API expose stage-level failure detail, and on what timeline? (b) Who owns provider keys long-term (also listed) — this gates Phase 2/3 sequencing.

## Minor / editorial

- The PRD references `/v1/me/uploads/bema`; confirmed it exists and the web UI calls it with `{episode, include_transcript: true}` (`upload-workspace.tsx:727-730`). Worth noting BEMA ingestion already has a server path, which interacts with Scenario D's "upload final asset to Podocracy" question.
- `POST /v1/me/projects/status` also exists (`files.py:1289`) in addition to the `GET`; not mentioned, not important.
- Date in document header (2026-06-14) matches; no action.

## Bottom line

Adopt the PRD's recommendation as-is. Before committing to the MVP scope and success criteria, resolve the four backend prerequisites in the gaps file (status detail, log download, healthcheck, and `yt-dlp` packaging if URL is in scope), or explicitly cut the dependent features from v1. The strategic content is sound; the risk is shipping an MVP definition that silently depends on backend capabilities that are not built yet.
