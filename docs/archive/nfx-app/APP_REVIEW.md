# Would an npx-based framework help refactor Podocracy?

**Reviewed:** `/Users/sergey.bazylko/Docs/pd-azdo/podcracy-app`
**Against pattern:** `test-npx-app` (this repo) / `npx @deepseek-ai/dsh web`
**Date:** 2026-09-14

---

## TL;DR

**No — do not refactor Podocracy's application onto an npx-based framework.** The
two are different classes of software. The npx pattern (a Node package you run
with `npx pkg web`) only works because the *entire app is Node/JS*. Podocracy's
core is **Python + heavy ML models + ffmpeg, orchestrated with Docker Compose** —
none of which can run inside a Node process. `npx` distributes and executes Node
packages; it cannot ship or run PyTorch, `pyannote.audio`, Whisper, or `yt-dlp`.

There **is** one narrow, legitimate use: replacing the fragmented cross-platform
**launcher/onboarding scripts** (~1,100 lines of bash + batch + PowerShell) with a
single `npx @podocracy/cli up` launcher. That is a genuine simplification for
*developers/CLI users*, but it should be an **optional extra entry point**, not a
replacement for the Docker stack or the double-click desktop app. See
[Recommendation](#recommendation).

---

## 1. What the npx pattern actually is (and is good for)

The `test-npx-app` in this folder — and `@deepseek-ai/dsh` — is:

- A **pure Node.js package** with a `bin` entry.
- `npx pkg web` runs that bin, which **starts a local HTTP server in the same Node
  process** and opens the browser.
- All logic (server, UI, business logic) is JavaScript/TypeScript that Node can
  execute directly.

It is an excellent pattern **when your whole app is Node/JS with light
dependencies**: zero install friction, one command, cross-platform for free,
trivial updates via npm. deepseek-harness qualifies because it is a TypeScript
monorepo — the runtime *is* Node.

That premise does not hold for Podocracy.

---

## 2. Podocracy's actual architecture

Podocracy is a self-hosted voiceover/translation pipeline. It has **no Node code
at all** — there is not a single `package.json` in the repo. It is three Docker
services plus a set of launchers:

| Component | Path | Stack | Role |
| --- | --- | --- | --- |
| **web** | `apps/web` | **nginx** + vanilla JS/HTML/CSS PWA (`app.js`, `improved.js`, `sw.js`, `manifest.webmanifest`) | Serves the UI, reverse-proxies `/api` to app-api. No build step, no Node. |
| **app-api** | `apps/app-api/api.py` | **Python** — FastAPI + uvicorn, python-multipart | Project CRUD, uploads, stage orchestration. |
| **worker** | `worker/` | **Python** — heavy ML/media | The compute core. |

The worker's dependencies (`worker/requirements.txt`) are the crux of the
argument:

- `pyannote.audio==4.0.7` → pulls in **PyTorch** for speaker diarization
- local **Whisper** (installed on first use) for speech-to-text
- `yt-dlp`, `pydub`, `mutagen`, `pysubs2` → media/audio (need **ffmpeg**)
- `openai`, `deepl`, plus optional ElevenLabs / local VibeVoice TTS
- `azure-storage-blob`, `opencensus-ext-azure`

Distribution today (from `README.md`, `docker-compose*.yml`, `scripts/`,
`packaging/`):

- **Prebuilt multi-arch images** on `ghcr.io`, run via `docker compose`.
- **Desktop wrappers** — a macOS `.app` (`scripts/make-macos-app.sh`) and a
  Windows `.cmd`/`.ps1` (`packaging/windows`, `scripts/podocracy-windows-run.ps1`)
  — the *primary, explicitly-non-technical* UX: "Download, double-click, paste
  your OpenAI key."
- **Launcher scripts** whose whole job is: check Docker is running → `docker
  compose up -d` → poll `/api/health` → open the browser. That logic lives in:

  | File | Lines |
  | --- | --- |
  | `scripts/podocracy-app-runtime.sh` | 391 |
  | `scripts/podocracy-windows-run.ps1` | 303 |
  | `scripts/launch.bat` | 105 |
  | `scripts/make-macos-app.sh` | 105 |
  | `scripts/_launch-common.sh` | 119 |
  | `scripts/launch.sh` + start/stop/restart | ~70 |
  | **Total launcher/onboarding surface** | **~1,100 lines across 3 languages** |

---

## 3. Why npx cannot replace the core

| Requirement | Docker (today) | npx / Node |
| --- | --- | --- |
| Run FastAPI/uvicorn (Python) | ✅ | ❌ Node can't execute Python |
| Run PyTorch + `pyannote.audio` | ✅ | ❌ No npm equivalent; can't ship torch |
| Run Whisper STT | ✅ | ❌ |
| ffmpeg / audio (`pydub`, `yt-dlp`) | ✅ system binary in image | ❌ not a Node concern |
| Pin exact Python + system libs | ✅ image is the contract | ❌ depends on user's machine |
| Isolate a multi-service stack | ✅ compose network | ❌ single process |
| Cache 5+ GB ML models on a volume | ✅ `pyannote-cache` volume | ❌ |

An `npx` package could at most **shell out** to `python`/`docker` — but then npx
is not running the app, it is just a launcher calling the exact same Docker stack.
Rewriting the Python worker in Node/TS to make it "npx-native" is a multi-month
rewrite that would *lose* access to the mature Python ML ecosystem the whole
product depends on. That is a non-starter.

**Conclusion:** the npx *application* pattern (`npx pkg web` starts the app
in-process) is architecturally impossible here.

---

## 4. Where npx *could* legitimately help: the launcher layer

Strip away the impossible part and one real opportunity remains. The ~1,100 lines
of bash/batch/PowerShell launcher code all implement the **same five steps** in
three languages. A single cross-platform Node CLI could collapse that:

```
npx @podocracy/cli up      # check docker → compose up → wait health → open browser
npx @podocracy/cli down
npx @podocracy/cli logs
npx @podocracy/cli update   # compose pull && up -d
```

This is essentially `test-npx-app`'s `web` command **plus** Docker orchestration
(the Docker-check / compose / health-poll / browser-open logic already written in
`scripts/_launch-common.sh`, ported to Node once instead of maintained ×3).

### Honest pros / cons of an npx launcher

**Pros**
- One cross-platform launcher codebase instead of bash + batch + PowerShell.
- Memorable, copy-pasteable: `npx @podocracy/cli up`.
- Trivial to update/version via npm; no "download the two script files" dance
  (README currently tells users to `curl` `launch.sh` + `_launch-common.sh`).
- Richer UX than shell: spinners, structured errors, interactive key prompt,
  version checks.

**Cons (why it is not a slam-dunk)**
- **Adds a Node.js prerequisite** for users who today need *only* Docker Desktop.
  That is a new install burden, not a reduction.
- **Requires a terminal.** The product's stated primary audience is non-technical
  ("no terminal"), served by the **double-click desktop app**. `npx` is strictly
  worse for them. It would replace bash/batch scripts (a dev concern), not the
  `.app`/`.cmd`.
- **Does not remove Docker** — the heavy dependency stays exactly as-is.
- **Yet another toolchain** to build, publish, and secure (npm supply chain) on
  top of the existing image + desktop-wrapper release pipeline (`docs/releases.md`).
- The native macOS `.app` can also *start Docker Desktop itself* and remember the
  chosen folder (`PODOCRACY_CONFIG_DIR`); an npx launcher would have to
  reimplement that and still lose the double-click.

---

## 5. Recommendation

1. **Keep the architecture as-is.** Docker Compose + Python/ML services is the
   correct design for this workload. An npx-based framework is not applicable to
   the application itself.

2. **Do not replace the desktop wrappers.** For the non-technical primary
   audience, the double-click `.app`/`.cmd` is the best UX and beats `npx`.

3. **Optional, low-priority:** consider a small `@podocracy/cli` npm package as an
   **additional developer/CLI entry point** that consolidates the shell/batch/PS
   launchers into one cross-platform Node CLI. Only worth it if you find the
   3-language launcher maintenance genuinely painful. It is a *convenience for
   people who already have Node*, not a strategic refactor. Scope it as a thin
   wrapper over `docker compose` — never as a rewrite of the Python core.

4. **If you do build the CLI**, `test-npx-app` in this folder is a usable starting
   skeleton (`bin/cli.js` arg-parsing + command dispatch, `src/server.js`
   browser-open + health patterns). Extend it with the Docker checks already
   proven in `scripts/_launch-common.sh` (`ensure_docker_running`, `compose_up`,
   `wait_for_portal`, `open_portal`).

---

## 6. Decision matrix

| Goal | Right tool | npx a fit? |
| --- | --- | --- |
| Run the Python/ML pipeline | Docker Compose | ❌ No |
| Ship to non-technical users, no terminal | Desktop `.app` / `.cmd` | ❌ No (needs terminal + Node) |
| One cross-platform launcher for developers | **npx CLI** or keep shell scripts | ⚠️ Optional, marginal win |
| Serve a self-contained Node/TS app | `npx pkg web` | ✅ Yes — but Podocracy isn't one |

**Bottom line:** The npx pattern is the wrong shape for Podocracy's core and a
downgrade for its primary users. Its only defensible role is an optional
developer-facing launcher that wraps the *unchanged* Docker stack — a nice-to-have,
not a refactor.
