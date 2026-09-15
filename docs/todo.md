# Worker Portal TODO

## PRD: Chunk-Level Voiceover (generate or record, per chunk)

Status: implemented. Section 13 records where the build diverged from this plan and what
is still unverified.

### 1. Problem

Voiceover today is a single all-or-nothing operation. `pd-050-voiceover.py` synthesizes every
chunk into a fresh timestamped scratch directory, then immediately assembles that directory into
the final mix. Three consequences:

- **Audio is not owned by a chunk.** The link between a chunk in `*.improved.json` and its audio
  file is positional and implicit: the synthesis filename is derived from the chunk's `start`/`end`
  timings, and the scratch directory name is a wall-clock timestamp. Nothing in the project records
  which chunk produced which file, with which voice, from which text.
- **You cannot fix one chunk.** If VibeVoice garbles chunk 37 out of 200, the only supported action
  from the portal is `POST /api/projects/{id}/voiceover`, which re-runs the whole synthesis. On a
  local 7B model that is hours. `pd-050-voiceover.py` does have a `--redo-segment` CLI flag, but it
  requires the caller to know the scratch directory path, and nothing in the API or the web UI
  exposes it.
- **Generating and recording are mutually exclusive modes.** `custom_recording` is a project-level
  boolean. When it is on, the legacy path expects a full set of recordings pulled from an Azure
  storage account (`prepare_custom_recording_dir`), which returns `None` for local projects, so
  self-recording is effectively unavailable in the app. There is no way to say "generate 198 chunks
  and record these 2 myself."

The `podocracy-tech` web app already solved the recording half of this. In
`apps/web/src/app/projects/[projectId]/improved/improved-workspace.tsx`, every chunk card renders a
`RecordingRow` with record / play / delete buttons. It records with `MediaRecorder`
(`audio/webm;codecs=opus` preferred), uploads the blob to a deterministic per-chunk filename
(`<base>.<index padded to 3>.m4a`), probes which chunks already have audio in one batched
`/v1/me/assets/check` call, and streams playback by fetching the asset and wrapping it in an object
URL. Audio is a per-chunk asset that exists independently of any pipeline run.

This PRD brings that model to `podcracy-app` and extends it to cover generated audio, so that a
chunk's audio is a first-class, addressable, replaceable artifact regardless of whether a TTS engine
or a microphone produced it.

### 2. Goals

- Every chunk owns exactly one audio segment, bound to the chunk in the improved JSON.
- Synthesis and assembly are separate stages. Assembly consumes the segment store and does not care
  how each segment got there.
- Regenerate a single chunk without touching any other chunk.
- Record a single chunk from the browser, in the improved editor, exactly like `podocracy-tech`.
- Mix freely: generated and recorded segments coexist in one project.
- Editing a chunk's text marks only that chunk's audio stale; it does not invalidate the project.
- Rebuild the final voiceover from existing segments with no synthesis at all.

### 3. Non-goals

- Per-chunk voice or engine selection in the UI. The data model reserves room for it (section 5.3
  records `engine` / `voice` / `model` per segment) but the UI ships with the project-level picker
  only.
- Multi-speaker VibeVoice scripts and speaker-driven voice assignment.
- Waveform display, in-browser trimming, or any audio editing beyond record / play / delete.
- Changing how `combinne_with_original_audio` aligns and mixes clips. Assembly logic stays as is.
- Migrating existing finished projects. Section 9 covers compatibility, not backfill.

### 4. Current behavior, for reference

- Live pipeline: `worker/worker_poll.py` polls `data/projects/project-*/status.json` for
  `state == "queued"`, takes an `fcntl` lock, and runs
  `worker/processing_container/pd-00-orchestrator.py -p <source>`. `worker/local_worker.py` is a
  parallel implementation that is **not** in the worker image (`worker/Dockerfile` copies
  `worker_poll.py`, `processing_container`, `common`, `stt`, `frontend`), so it is out of scope as a
  target, though its `segment_key` / `load_custom_recordings` helpers are useful references.
- Stage list lives in `pd-00-orchestrator.py` as `("voiceover", ".../pd-050-voiceover.py", ["-p", path])`,
  selected via the `stages_to_run` param.
- `pd-050-voiceover.py::main` does: load `*.improved.json` → `tts()` writes
  `<scratch>/<start>-<end>.ogg` per chunk → `update_tts_audio()` applies `atempo` into a second
  scratch directory → `combinne_with_original_audio()` mixes against the source MP3.
- Chunk shape is `{speaker, text, start, end?, dltrans, imp}`. `improved_text_key` is `imp` and
  `translation_text_key` is `dltrans` (`apps/app-api/api.py:425`). `end` is optional; the assembler
  handles both `0000.ogg` and `0000-0000.ogg` filename forms via `valid_tts_filename_format`.
- `apps/app-api/api.py` exposes `POST /api/projects/{id}/voiceover`, which rewrites `params.json`
  with `stage_preset=voiceover`, `stages_to_run=voiceover`, `resume_from_improved=True` and sets
  `status.json` to `queued`.
- `apps/web/improved.js` renders the chunk editor. `normalizeChunk()` rebuilds each chunk from a
  fixed field list, so **any field the worker adds to a chunk is silently dropped on save today**.
  Fixing this is a hard prerequisite (section 9.2).
- The app-api image has no `ffmpeg`; the worker image does.

### 5. Design

#### 5.1 Stable chunk identity

Timings are editable, so they cannot be the key. Add a `chunk_id` field to every chunk in
`*.improved.json`.

- Format: `c` + zero-padded ordinal at assignment time, e.g. `c000`, `c001`.
- Assigned on first write of the improved transcript. For transcripts that predate this change,
  assigned lazily by position the first time the file is loaded by the API or the worker, then
  persisted.
- Ids are never reused. Inserting a chunk allocates `max(existing) + 1`. Deleting a chunk retires
  its id.
- Reordering, retiming, and text edits all preserve `chunk_id`.

#### 5.2 Segment store

One directory per project: `data/projects/<project-id>/work/segments/`.

```
work/segments/
  segments.json          # store index, worker- and API-writable
  c000.ogg               # canonical audio, 1 per chunk, mono, 24 kHz, Ogg/Vorbis
  c001.ogg
  raw/
    c001.webm            # original browser upload, kept for re-processing
  orphaned/
    c042.ogg             # audio whose chunk was deleted
```

Canonical format is Ogg to match what the legacy assembler globs for (`src_format = "ogg"` in both
`update_tts_audio` and `combinne_with_original_audio`) and what `tts_vibevoice` already requests
from the server.

`segments.json` is the store's own index and the recovery source if the improved JSON is
hand-edited or replaced:

```json
{
  "version": 1,
  "segments": {
    "c001": {
      "file": "c001.ogg",
      "raw_file": "raw/c001.webm",
      "source": "recording",
      "duration_ms": 4210,
      "bytes": 38112,
      "text_hash": "sha256:9f2c…",
      "voice_hash": "sha256:11ab…",
      "engine": null,
      "voice": null,
      "model": null,
      "created_at": "2026-09-14T10:04:11Z",
      "status": "ready"
    }
  }
}
```

#### 5.3 Chunk to audio binding in the improved JSON

Per the requirement that each generated segment is attached to its chunk, the worker writes a
compact `audio` object back into the chunk:

```json
{
  "chunk_id": "c037",
  "speaker": "Marty Solomon",
  "start": "0738",
  "end": "0749",
  "text": "…",
  "dltrans": "…",
  "imp": "…",
  "audio": {
    "file": "work/segments/c037.ogg",
    "source": "tts",
    "engine": "vibevoice",
    "voice": "SEBBE",
    "model": "7B",
    "duration_ms": 10840,
    "text_hash": "sha256:4c81…",
    "generated_at": "2026-09-14T09:58:02Z",
    "status": "ready"
  }
}
```

`status` is one of:

| status | meaning |
| --- | --- |
| `ready` | audio exists and matches the current text and voice settings |
| `stale` | audio exists but `text_hash` or `voice_hash` no longer matches the chunk |
| `missing` | no audio for this chunk |
| `skipped` | `imp` is empty, so no audio is expected; the assembler skips it |
| `failed` | last synthesis attempt errored; `error` holds a truncated message |

`segments.json` is authoritative when the two disagree, because the improved JSON is user-editable.
On load, the worker reconciles by `chunk_id` and rewrites the `audio` block from the store.

#### 5.4 Staleness

- `text_hash` = `sha256(chunk[improved_text_key].strip())`.
- `voice_hash` = `sha256` over the synthesis-relevant params: `tts_api`, `voice`, `vibevoice_model`,
  `vibevoice_speed`, `vibevoice_cfg_scale`, `openai_model_tts`.
- A mismatch on either marks a `tts` segment `stale`.
- **A `recording` segment is never marked stale by a voice-param change and is never regenerated
  automatically.** A text change flags it `stale` for display only; the user decides whether to
  re-record. This protects work the user cannot cheaply reproduce.

#### 5.5 Pipeline split

`pd-050-voiceover.py` gains a `--mode` argument and splits internally:

- `--mode synthesize`: iterate chunks, generate audio only where `status` is `missing` or `stale`
  **and** `source != "recording"`. Write into the segment store, update `segments.json` and the
  improved JSON after each chunk so a crash halfway through loses at most one chunk. Never writes
  the final mix.
- `--mode build`: materialize a staging directory from the segment store using legacy filenames
  (`<start>-<end>.ogg`, or `<start>.ogg` when `end` is absent), apply tempo, then run the existing
  `update_tts_audio` + `combinne_with_original_audio` + postprocess path unchanged. Chunks whose
  status is `missing` or `failed` are logged and skipped; the build still produces output.
- `--mode all` (default, current behavior): `synthesize` then `build`.
- `--chunks c012,c037`: restrict `synthesize` to an explicit id list and force regeneration of those
  ids even if they are `ready` or are recordings.

The staging directory is the seam that keeps the legacy assembler untouched: retiming a chunk
changes only the staged filename, never the stored file.

Orchestrator stage table in `pd-00-orchestrator.py` gets two additional entries that map to the same
script:

```python
("tts",             f"{d}/pd-050-voiceover.py", ["-p", path, "--mode", "synthesize"]),
("voiceover-build", f"{d}/pd-050-voiceover.py", ["-p", path, "--mode", "build"]),
("voiceover",       f"{d}/pd-050-voiceover.py", ["-p", path, "--mode", "all"]),
```

`--chunks` is not passed on the command line; it is read from `params["voiceover_chunks"]` so that
`worker_poll.py` needs no changes (it always invokes the orchestrator the same way, and params are
refreshed from `config/params.json` on each run).

#### 5.6 Tempo and cleanup, per source

Today `custom_recording` forces `custom_speedup = 1` for the entire project. With mixed sources that
becomes per segment:

- `source == "tts"` → `voiceover_tempo` (default `speedup_value`, legacy `1.2`).
- `source == "recording"` → `1.0`, overridable per project via a new `recording_tempo` param.

Recording cleanup, currently `process_custom_recording` (loudness normalize, `shared_clicks_removal.py`,
ffmpeg pause removal), runs in the worker on ingest, not in the API, because the app-api image has no
ffmpeg. The API stores the browser upload under `raw/` and marks the segment `pending_ingest`; the
next `synthesize` or `build` run converts `raw/c001.webm` to `c001.ogg` with cleanup applied. The
`raw/` copy is retained so cleanup settings can change without asking the user to re-record.

Browsers can play the raw WebM/Ogg directly, so playback in the editor works before ingest.

#### 5.7 API surface

New endpoints in `apps/app-api/api.py`:

| method | path | purpose |
| --- | --- | --- |
| `GET` | `/api/projects/{id}/segments` | one batched call returning every chunk's id, timings, status, source, duration, and a text preview. Mirrors `podocracy-tech`'s `/v1/me/assets/check`: the editor must not issue one request per chunk. |
| `GET` | `/api/projects/{id}/segments/{chunk_id}/audio` | stream the canonical file, falling back to the raw upload when ingest has not run |
| `PUT` | `/api/projects/{id}/segments/{chunk_id}/audio` | multipart upload of a recording; accepts `webm`, `ogg`, `m4a`, `mp3`, `wav`; writes `raw/<chunk_id>.<ext>`, sets `source: "recording"`, `status: "pending_ingest"` |
| `DELETE` | `/api/projects/{id}/segments/{chunk_id}/audio` | remove canonical and raw files, set `status: "missing"` |
| `POST` | `/api/projects/{id}/segments/{chunk_id}/regenerate` | queue a job with `stages_to_run=tts`, `voiceover_chunks=[chunk_id]` |
| `POST` | `/api/projects/{id}/voiceover/synthesize` | queue `stages_to_run=tts` for all missing and stale chunks |
| `POST` | `/api/projects/{id}/voiceover/build` | queue `stages_to_run=voiceover-build`, no synthesis |

`POST /api/projects/{id}/voiceover` keeps its current meaning (`stages_to_run=voiceover`, synthesize
missing then build) so existing callers and the home page button are unaffected.

`chunk_id` is validated against `^c\d{3,}$` before it touches the filesystem. Uploads are capped
(`SEGMENT_UPLOAD_MAX_BYTES`, default 25 MB) and the extension is allowlisted.

#### 5.8 Web UI

`apps/web/improved.js`, following `improved-workspace.tsx`:

- **Per chunk**, below the "Improved text" field, a `SegmentRow`:
  - 🎤 record / ⏹ stop, using `MediaRecorder` with the same preferred-mime probe as `podocracy-tech`
    (`audio/webm;codecs=opus`, `audio/webm`, `audio/ogg;codecs=opus`, `audio/ogg`), stopping all
    tracks on `onstop`, then `PUT`ing the blob.
  - ▶ play / ⏸, via `fetch` → `blob()` → `URL.createObjectURL`, revoking the previous object URL,
    with the suppress-error-on-delete guard that the reference implementation uses.
  - 🗑 delete, behind a confirm.
  - ⟳ regenerate this chunk, disabled when a job is already running for the project.
  - A status badge: `Generated` / `Recorded` / `Text changed since generation` / `No audio` /
    `Failed` / `Processing…`, plus duration.
- **Toolbar**, alongside the existing "Start voiceover": "Generate missing" and "Build voiceover".
- Status is loaded once via `GET /segments` after the transcript loads, and refreshed by polling
  `GET /api/projects/{id}` while `state` is `queued` or `running`.
- Recording and regeneration are disabled while the project has a running job, and the editor shows
  which chunk the worker is currently on.
- A chunk with unsaved text edits disables ⟳ with the hint "Save first"; regenerating against the
  on-disk text would silently produce the wrong audio.

### 6. Job and queue semantics

`worker_poll.py` keeps its single-flight, one-project-at-a-time model and needs no change. Everything
is expressed through `config/params.json` plus `status.json`:

- The API writes `stages_to_run` and, for single-chunk work, `voiceover_chunks`, then sets
  `status.json` to `state: "queued"` with a `job_kind` field (`voiceover`, `tts`, `voiceover-build`,
  `tts-chunk`) so the UI can label progress accurately.
- The API refuses to queue when `state` is `queued` or `running`, returning `409` with the current
  stage. The editor surfaces this rather than silently dropping the click.
- Single-chunk regeneration of one chunk is a full project job in the queue. That is acceptable for a
  local single-user app and avoids inventing a second execution path. It does mean the project shows
  as busy for the duration.
- `voiceover_chunks` is cleared from `params.json` by the worker when the stage completes, so a later
  full run is not accidentally scoped.

### 7. Data formats summary

| artifact | path | writer |
| --- | --- | --- |
| improved transcript with `chunk_id` + `audio` | `work/<stem>.improved.json` | worker, editor |
| segment index | `work/segments/segments.json` | worker, app-api |
| canonical segment audio | `work/segments/<chunk_id>.ogg` | worker |
| raw browser upload | `work/segments/raw/<chunk_id>.<ext>` | app-api |
| retired audio | `work/segments/orphaned/<chunk_id>.ogg` | worker |
| assembly staging | `work/segments/.staging-<timestamp>/<start>-<end>.ogg` | worker, disposable |

### 8. Edge cases

- **Chunk deleted in the editor.** Its `chunk_id` disappears from the improved JSON. On the next
  worker run, the store moves the file to `orphaned/` rather than deleting it, so an accidental
  delete is recoverable.
- **Chunk with empty `imp`.** Status `skipped`. No synthesis, no staged file, no assembler entry.
  This matches today's `Empty text in chunk` log branch.
- **Chunk with no `end`.** Staged as `<start>.ogg`; `get_segment_end_seconds` already derives the end
  from the following filename.
- **Two chunks with identical `start`/`end`.** The staging filename would collide. The build fails
  fast with both `chunk_id`s named, rather than silently dropping one, because today's positional
  glob would drop one without notice.
- **Improved JSON replaced wholesale** (user pastes new content, or re-runs `improve`). `chunk_id`s
  are gone, so ids are reassigned from `c000` by position and collide with the entries already in
  the store. `text_hash` is what makes that safe: a reassigned id whose text does not match its old
  entry reads as `stale` and is regenerated, and one whose text matches exactly reuses the audio,
  which is the right answer whichever position it came from. Entries with no chunk left to claim
  them move to `orphaned/`.
- **Recording uploaded for a chunk that already has generated audio.** The recording wins; the
  generated file is removed and `source` flips to `recording`.
- **Regeneration fails on a chunk that already had audio.** Synthesis writes aside and moves into
  place only on success, so the previous take survives. The entry keeps its old status and gains an
  `error` the editor shows, rather than becoming an unusable `failed` with a truncated file.
- **Synthesis fails mid-run.** Completed chunks are already persisted, so the retry only covers the
  remainder. This is the single biggest practical win of the split.
- **Concurrent edits.** The editor holds the improved JSON in memory and `PUT`s the whole file. If the
  worker has written `audio` blocks in the meantime, the editor's save would revert them; section 9.2
  is what prevents that.

### 9. Migration and compatibility

1. **Existing projects.** No `chunk_id`, no segment store. On first load, ids are assigned by position
   and persisted; all chunks report `missing`; the first voiceover run populates the store. Finished
   projects keep their existing output artifacts untouched.
2. **The editor must stop dropping unknown fields.** `normalizeChunk()` in `apps/web/improved.js`
   rebuilds chunks from a fixed field list, which would delete `chunk_id` and `audio` on every save.
   Change it to spread the source object and normalize only the known fields, and make
   `serializeChunks()` round-trip the rest. This is a prerequisite for everything else and should land
   first, on its own, so it can be verified independently.
3. **`custom_recording` param.** Kept as a project-level shortcut meaning "expect recordings for all
   chunks" and as the flag that turns on the tempo rule of section 5.6. The zip-upload path stays as a
   bulk-import convenience: on ingest it fans out into the segment store by matching filenames to chunk
   ids the way `local_worker.load_custom_recordings` already matches (`<start>-<end>`, `<start>`,
   positional index).
4. **`--redo-segment`.** Superseded by `--mode synthesize --chunks`. Keep the flag as an alias for one
   release, logging a deprecation line.

### 10. Acceptance criteria

Verified by the automated suites (`worker/test_segment_store.py`, `apps/app-api/test_api.py`):

- [x] Editing one chunk's improved text and saving flips exactly that chunk to
      `Text changed since generation`; every other chunk stays `Generated`.
- [x] "Generate missing" regenerates only the stale and missing chunks. A second run over an
      already-generated project makes zero TTS calls.
- [x] The per-chunk regenerate button regenerates that chunk only, and unscopes itself so the
      next full run is not silently restricted to it.
- [x] A recorded chunk is never regenerated by a gap-filling run, and a voice-setting change
      does not invalidate it.
- [x] Generated chunks are staged at `voiceover_tempo`, recordings at `1.0`, in one mix.
- [x] Synthesis that fails on one chunk keeps every chunk that succeeded, marks the failure on
      the chunk, and fails the stage.
- [x] Saving from the editor preserves `chunk_id`, `audio`, and pipeline-only fields such as
      `dltrans`.
- [x] Deleting a chunk retires its audio to `orphaned/` and does not break the build.
- [x] A chunk whose improved text is blank produces no audio and is not a hole in the build.
- [x] Two chunks with identical timings fail the build loudly instead of one being dropped.
- [x] Every mutating endpoint returns `409` while a job is running; listing still works.

Still to confirm by hand, because they need a browser, a microphone, ffmpeg and a real model:

- [ ] Record a chunk in the browser, then "Build voiceover", and hear the recording in place of
      the generated audio for that chunk.
- [ ] A project where every chunk is recorded matches the old `custom_recording` output.
- [ ] Playback of each chunk in the editor, including a recording that has not been ingested yet.
- [ ] Kill the worker mid-synthesis, re-queue, and confirm it resumes rather than restarting.

### 11. Work breakdown

- [x] Make `apps/web/improved.js` round-trip unknown chunk fields.
- [x] Add `chunk_id` assignment and persistence, shared between `apps/app-api/api.py` and the worker.
- [x] Implement the segment store: `worker/common/segment_store.py` with layout, `segments.json`
      read/write, hashing, status computation, and orphan handling.
- [x] Split `pd-050-voiceover.py` into `synthesize` / `build`, add `--mode` and `voiceover_chunks`,
      add the staging-directory materialization.
- [x] Add per-source tempo and move recording cleanup into worker-side ingest.
- [x] Register the `tts` and `voiceover-build` stages in `pd-00-orchestrator.py` and extend
      `STAGE_OPTIONS` in `apps/app-api/api.py`.
- [x] Add the seven segment endpoints, with `chunk_id` validation, upload limits, and `409` on busy.
- [x] Add `job_kind` to `status.json` and surface it in the editor.
- [x] Build the per-chunk audio row in `apps/web/improved.js` and the two toolbar actions.
- [x] Tests for the endpoints, the status rules, staging, and an end-to-end synthesize run.
- [x] Document the flow in `docs/local-tts-vibevoice.md` and `README.md`.

### 12. Resolved questions

- **Per-chunk `text_preview` in `GET /segments`?** No. The endpoint returns `chunk_id`, timings and
  speaker; the editor already holds the text and matches on `chunk_id`.
- **Auto-rebuild after a single-chunk regeneration?** No. Fixing several chunks in a row would
  waste a full assembly each time. Press "Build voiceover" when done.
- **Upload cap?** 25 MB, overridable with `SEGMENT_UPLOAD_MAX_BYTES`.

### 13. Deviations from the plan, and known gaps

- **Stale audio is assembled, not skipped.** Section 5.5 only ever listed `missing` and `failed`
  as skipped, but it was worth making explicit: a chunk whose text moved on still ships its old
  audio, because a silent hole in the mix is worse. The build logs every stale chunk it included.
- **Blank vs absent improved text now mean different things.** `imp: ""` means "no audio for this
  chunk", which is how the editor mutes one. A missing `imp` key still falls back to `dltrans`,
  because that means the improve stage never ran. The previous fallback voiced a deliberately
  blanked chunk from its translation.
- **The store logic is duplicated, not shared.** `apps/app-api/segments.py` mirrors
  `worker/common/segment_store.py` because the two images build from different Docker contexts
  (`./apps/app-api` and `./worker`), and moving both to a repo-root context would need a
  `.dockerignore` to keep `data/projects` out of the build. `worker/test_segment_store.py` loads
  both files and asserts they agree on constants, hashing, id assignment, chunk-text rules and the
  full status matrix, so drift fails the suite.
- **One transcript resolver.** `improved_artifact_for_project` now prefers the file named after
  the source stem, which is the one the worker reads and writes. It previously preferred
  `work/source.improved.json`, so the editor and the pipeline could end up on two copies. They
  were identical in the projects on disk, so this is a removed risk rather than an observed bug.
- **Click removal stays best effort.** The legacy cleanup shells out to `shared_clicks_removal.py`,
  which imports `webrtcvad`. That package is not in `worker/requirements.txt` and needs a compiler
  the slim image does not have, so ingest logs a warning and keeps the un-declicked audio. The
  ffmpeg pause trim and loudness normalization still run.
- **A single-chunk regeneration occupies the whole project.** The worker runs one project at a
  time, so the project shows as busy for the duration and the editor disables its controls. Fine
  for a local single-user tool; it would need a real job queue to be anything else.
- **Legacy flags are deprecated, not removed.** `--redo-segment` maps onto `--chunks`, and `--dir`
  now imports its contents into the store instead of being assembled directly. Both log a warning.

## Legacy Worker Parity


- [ ] Restore full legacy parameter surface where still relevant.
  - Examples still not surfaced: `sleep_time_tts`, `openai_model`, `openai_model_tts`, `whisper_default_model_local`, `window_size_for_timesync`, `time_delta_max_for_timesync`, `deepl_delay`, `whisper_api_max_size_mp3`, `number_of_issues_to_find`, `improve_openai_model`, and `improve_openai_temperature`.
  - Current gap: the portal covers the main workflow params and some legacy defaults, but not every `parameters.json` option yet.
