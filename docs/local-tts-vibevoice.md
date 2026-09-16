# Local TTS with VibeVoice

Podocracy can send the voiceover stage to a self-hosted, OpenAI-compatible TTS server
instead of a paid cloud API. Selecting the `vibevoice` engine means the narration costs
nothing per minute, the script text never leaves the machine, and the narrator can be a
zero-shot clone of your own voice.

Podocracy does **not** install, bundle, or supervise the VibeVoice server. It is a separate
process you start yourself; Podocracy only talks to it over HTTP.

## Prerequisites

- A 24 GB CUDA GPU, or an Apple Silicon Mac (MPS).
- About 22 GB of disk for the model weights.
- The VibeVoice local TTS server from the sibling `vibevoice` repository.

## 1. Start the server

Start VibeVoice so it listens on `127.0.0.1:8765`. Confirm it is up:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/v1/voices
```

### Port 8000 is not a conflict

The Podocracy `app-api` container also uses port 8000, but only `expose`s it inside the
Compose network — it is never published to the host. VibeVoice binding host port 8000 does
not collide with it. The only published Podocracy port is `PORTAL_HTTP_PORT` (default 8080).

## 2. Point Podocracy at it

Uncomment and set these in your `.env` (see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `VIBEVOICE_BASE_URL` | `http://host.docker.internal:8765/v1` | Root of the OpenAI-compatible API. Setting a value is what marks the provider configured. |
| `VIBEVOICE_API_KEY` | empty | Bearer token; sent only when non-empty. |
| `VIBEVOICE_TTS_MODEL` | `7B` | Checkpoint or alias sent as `model`. |
| `VIBEVOICE_TTS_VOICE` | `SEBBE` | Fallback voice when the project has none. |
| `VIBEVOICE_TIMEOUT_SECONDS` | `900` | Per-request read timeout, sized for cold start plus a long segment. |
| `VIBEVOICE_CFG_SCALE` | empty | Optional guidance scale 1.0–3.0; omitted from the request when empty. |
| `VIBEVOICE_SPEED` | `1.0` | Optional pitch-preserving speed 0.25–4.0. |

Restart the stack after editing `.env`.

### Why `host.docker.internal` and not `127.0.0.1`

The Podocracy worker always runs inside a container. Inside that container `127.0.0.1` is
the container itself, not your machine. `host.docker.internal` resolves to the host; the
Compose files map it explicitly via `extra_hosts: host-gateway` so this works on Linux and
Colima as well as Docker Desktop.

If you run the worker **outside** Docker, set `VIBEVOICE_BASE_URL=http://127.0.0.1:8765/v1`.

`VIBEVOICE_BASE_URL` and `VIBEVOICE_API_KEY` are read from the environment only. They are
deliberately not project parameters: a per-project base URL would let anyone who can post to
the portal aim the worker's requests at arbitrary hosts inside the Docker network.

## 3. Narrate in your own voice

Record a clean 20–40 second reference clip and drop it into the server's `voices/` folder,
then restart the server. The clip's name becomes the voice id.

In the portal, choose **VibeVoice (local)** under *Voiceover engine*. The **Narrator voice**
list repopulates from `GET /v1/voices` on your running server, so your own cloned voices
appear there — no file editing required. If the server is unreachable, the field falls back
to free text so you can still type a voice id and submit.

## Fixing one chunk without redoing the episode

Audio is stored per chunk, not per run. Open a project's improved transcript in the editor
and every chunk gets its own audio row:

| Control | What it does |
| --- | --- |
| 🎤 | Record this chunk yourself, straight from the browser |
| ▶ | Play whatever audio the chunk currently has |
| ⟳ | Regenerate just this chunk with the project's TTS engine |
| 🗑 | Delete this chunk's audio |

The badge next to the controls says where the audio came from and whether it is current:
*Generated*, *Recorded*, *Text changed since generation*, *No audio*, or *Failed*.

Two toolbar buttons complete the loop:

- **Generate missing** synthesizes only the chunks that are missing or stale. On a 200-chunk
  episode with one bad chunk, that is one TTS call rather than 200.
- **Build voiceover** assembles the final mix from whatever audio exists, with no synthesis
  at all.

Recording and generating mix freely in one project: generate the episode, re-record the three
chunks the model mangled, then build. Recordings are never overwritten by a *Generate missing*
run, and changing the narrator voice does not invalidate them. Generated chunks get the
project's `voiceover_tempo` (legacy default `1.2`); recordings are left at `1.0`, overridable
with the `recording_tempo` param.

Editing a chunk's text marks that one chunk stale and leaves the rest alone. Save before
recording or regenerating: both stamp the chunk's saved text, so unsaved edits would produce
audio that is marked stale the moment it lands. The editor disables those buttons until you do.

Behind the scenes the audio lives in `<project>/work/segments/`, one `<chunk_id>.ogg` per
chunk, indexed by `work/segments/segments.json` and mirrored onto each chunk in the improved
transcript as an `audio` block. Deleting a chunk moves its audio to `work/segments/orphaned/`
rather than destroying it.

Browser recordings are converted by the worker, not the portal API, because only the worker
image carries ffmpeg. A recording is therefore playable immediately but shows as *Recorded,
not yet processed* until the next run converts and cleans it up. Cleanup is loudness
normalization, then voice-activity click and long-pause removal, then an ffmpeg trim of the
shorter pauses. Set `recording_cleanup` to `false` on a project to keep raw takes as recorded.

The voice-activity step is deliberately non-fatal. If it crashes, or returns audio that is
empty or less than a quarter of what went in, the log says so and the un-declicked audio is
kept: a recording the user cannot cheaply redo is worth more than a clean one, and an
aggressive voice-activity verdict on a quiet take would otherwise replace it with silence.

## Two speakers in the same second

Chunk timings have one-second resolution, so a fast exchange can put two chunks at the same
`start`/`end`. Assembly is keyed on those timings, so both clips are staged as a single file with
their audio joined in transcript order and a 150 ms gap between them. Nothing is lost, and the
build logs which slots it merged.

This is common in a two-host show. Before the segment store, both clips were written to the same
filename and the first was silently dropped from the mix, so some older projects are missing a line
or two even though they reported success.

## Stopping a run

**Stop processing** on the project page, or in the improved-transcript editor, ends a run without
restarting anything. Chunks already generated stay in the store, so picking up again with
**Generate missing** only does the remainder.

The button also covers the case where a run is no longer being driven, after a worker restart, for
example. It then reads **Reset stuck state** and clears the status so the project can be started
again. The worker resets orphaned runs by itself on startup too, so this is a manual override
rather than the only route out.

A stop takes effect within a second or two. Whatever chunk was mid-synthesis is discarded, but the
previous version of that chunk's audio is untouched: synthesis writes to a temporary file and only
moves it into place on success.

## Expected throughput

Synthesis runs roughly **2.4× slower than real time** on an M-series Mac at fp16, and the
first request after startup also pays the model load. A 20-second segment can take ~50 s, and
a 78-minute episode is an overnight job, not a coffee break. The worker logs every segment
with its elapsed time so a slow run is distinguishable from a hang.

Podocracy sends segments one at a time on purpose. The server loads one checkpoint onto one
device; concurrent requests would contend for the same GPU/MPS memory rather than go faster.

## Model choice

`7B` is the default and the quality option. `1.5B` is faster and fits a 16 GB GPU, but
upstream measurements show it inflating a script through repetition (444 s of source becoming
624 s of audio). Treat `1.5B` as the constrained-hardware fallback.

## Security

The VibeVoice server ships with **no authentication**, because it is meant to bind to
localhost. If you move it to a reachable host:

- set `VIBEVOICE_API_KEY` on both the server and in Podocracy's `.env`,
- restrict the firewall to the hosts that need it,
- put TLS in front of it.

An open `/v1/audio/speech` on a GPU box is free compute for whoever finds it, billed to you.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Job fails at `tts-preflight` with "VibeVoice server not reachable" | Server is stopped, or the URL is wrong | Start the server; check `VIBEVOICE_BASE_URL` and `curl` it from the host |
| Same error, but `curl` from the host works | Container cannot resolve the host | Confirm `extra_hosts: "host.docker.internal:host-gateway"` is present and the stack was recreated |
| Voiceover engine option shows "set VIBEVOICE_BASE_URL" | Variable is unset or empty in the env file the containers load | Uncomment it in `.env`, then restart the stack |
| Narrator voice falls back to a text box | The voices call failed or returned nothing | Check `GET /v1/voices` on the server; the job still runs if you type a valid voice id |
| `HTTP 400: unknown voice 'X'` | The voice id is not on the server | Pick from the dropdown, or add the reference clip to `voices/` and restart the server |
| Job fails partway with a read timeout | A segment took longer than the timeout | Raise `VIBEVOICE_TIMEOUT_SECONDS`, or switch to the `1.5B` model |
| Voiceover sounds sped up or slowed down | `voiceover_tempo` (default 1.2) is applied by ffmpeg after synthesis | Set Voiceover speed to 1.0 in Advanced settings, and use `VIBEVOICE_SPEED` instead |
