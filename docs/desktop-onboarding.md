# Beginner-Friendly Desktop Onboarding

This document proposes how to turn the current "download a compose file, create a
`.env`, run a Docker command, open a URL" flow into a **double-click desktop app**
experience for non-technical users, with tailored paths for macOS and Windows.

It is both a design proposal and a description of the reference implementation that
ships in this repository (`scripts/make-macos-app.sh`, `scripts/podocracy-app-runtime.sh`,
and `scripts/podocracy-windows-run.ps1`).

---

## 1. Who we are optimizing for

A beginner user who:

- Has never used a terminal.
- Does not know what Docker, Compose, a port, or an environment file is.
- Has an OpenAI API key pasted somewhere (or needs to be told where to get one).
- Wants to click one icon, wait, and have the app open in their browser.

Everything below is measured against a single question: **can this person get from a
fresh laptop to a working portal without ever opening a terminal?**

---

## 2. Current friction (from a beginner's view)

Today's fastest path (README "Quick Start" / "One-click launch") still requires the
user to:

1. Understand and install Docker.
2. Create a folder in the right place.
3. Copy `.env.example` to `.env` and hand-edit it with an API key.
4. Download `docker-compose.images.yml` (and the launcher scripts) with `curl`.
5. Run a shell/batch script from the correct directory.
6. Know that "first start is slow" is normal, not a hang.

Each numbered step is a place where a beginner drops off. Steps 2–5 are all terminal
work. Step 3 is the single most error-prone step (wrong file name, committed secret,
quotes around the key, etc.).

The launcher scripts (`scripts/launch.sh`, `scripts/launch.bat`) already solve
"start containers + wait for health + open browser." What is missing is a **no-terminal
wrapper** around them plus a **first-run setup** that handles Docker, the app folder,
and the API key.

---

## 3. Design principles

1. **One icon, one behavior.** Double-click → the portal opens in the browser →
   the launcher quits. No visible terminal in the normal case.
2. **First run does setup; later runs just launch.** Detect what is missing and only
   prompt for it once.
3. **Never ask a beginner to edit a file.** Collect the API key through a dialog and
   write `.env` for them.
4. **Fail loudly and kindly.** When Docker is missing or a container fails, show a
   plain-language dialog with the one action to take, not a stack trace.
5. **Reuse the tested launcher logic.** The desktop wrapper must call the same
   Docker/Compose/health/open logic as `scripts/_launch-common.sh`, not fork it.
6. **Keep data in a predictable, user-visible place.** One "app home" folder holds the
   compose file, the `.env`, and all projects.

---

## 4. The "app home" folder

Introduce a single home directory that the desktop app owns and the user can find:

```
~/Podocracy/
├── docker-compose.images.yml   # downloaded/updated by the app
├── .env                        # written by the first-run wizard
├── logs/
│   └── launch.log              # last launch output for support
└── projects/                   # all user projects (bind-mounted into containers)
```

- Overridable with `PODOCRACY_HOME` for power users.
- `PODOCRACY_PROJECTS_DIR` is set to `~/Podocracy/projects` so projects live in a
  stable, visible location that survives image updates and reinstalls (this fixes the
  "projects don't follow you" caveat in `docs/releases.md`).
- Putting it in the user's home folder (not `~/Library/Application Support` or
  `%AppData%`) means a beginner can actually open it in Finder/Explorer to see their
  output files.

### 4.1 Will `~/Podocracy` work as a bind mount? (yes)

The containers bind-mount the projects folder into `/data/projects`
(`${PODOCRACY_PROJECTS_DIR:-./projects}` in `docker-compose.images.yml`). `~/Podocracy`
is a safe default because:

- **It is inside the auto-shared home directory.** Docker Desktop on macOS shares
  `/Users` by default, and the WSL2 backend on Windows shares the user profile
  automatically, so `~/Podocracy` (`/Users/<name>/Podocracy` or
  `C:\Users\<name>\Podocracy`) can be bind-mounted with no File Sharing changes. A path
  under `~` is specifically chosen to avoid the "path is not shared from the host" error
  that hits folders outside the shared roots.
- **No permission surprises.** With the Docker Desktop VM, files written to the bind
  mount appear owned by the user on the host, so projects are readable/editable in
  Finder/Explorer.
- **The Compose project name stays stable.** Because the launcher always runs from
  `~/Podocracy`, Compose derives the same project name (`podocracy`) every time, so it
  reuses the same containers/volumes instead of creating duplicates.
- **`.env` auto-loads.** Compose reads `.env` from the working directory, and the
  launcher `cd`s into `~/Podocracy` before starting, so keys load without `--env-file`.

Minor things to keep in mind (all handled by the scripts):

- **Spaces in the home path** (e.g. `C:\Users\First Last`) — the launchers quote paths, so
  this works, but it is the main reason to keep the folder name itself simple.
- **iCloud** — `~/Podocracy` is deliberately not under `~/Desktop` or `~/Documents`, which
  can be synced to iCloud on macOS; you do not want large media/artifacts syncing to the
  cloud.
- **Linux** (not the primary beginner target) can hit root-owned files from the bind
  mount; Docker Desktop for Mac/Windows does not.

### 4.2 Adopting an existing command-line setup

Users who already ran the CLI Quick Start have a folder (often
`~/podocracy-worker-portal`) with their own `.env` and compose file. The desktop app must
not ignore that and silently create a second, empty setup. So on the **first run** it asks
which folder to use:

1. **Auto-detect.** It looks for an existing setup in the common spots
   (`~/podocracy-worker-portal`, `~/Podocracy`, `~/podocracy`). A folder "counts" if it
   already contains a `.env` or a compose file.
2. **Offer choices.** If one is found: *Use this one* / *Choose another…* / *Create new*.
   If none is found: *Choose existing…* (a native folder picker) / *Create new*.
3. **Respect what's already there.** When the chosen folder already has:
   - a `.env` → the API-key wizard is **skipped** (the existing keys are detected and
     reused), and its `PODOCRACY_PROJECTS_DIR` and `PORTAL_HTTP_PORT` are honored, so the
     user's existing projects and custom port keep working and the browser opens on the
     right port;
   - a compose file (images **or** a source checkout's `docker-compose.yml`) → nothing is
     downloaded, so the adopted setup is never clobbered.
4. **Remember the choice.** The selected folder is saved to a tiny config file
   (`~/.config/podocracy/home.path` on macOS, `%APPDATA%\Podocracy\home.path` on Windows),
   so every later launch goes straight to that folder without asking again.

Power users can still force a folder with `PODOCRACY_HOME=/path`, and can re-trigger the
chooser by deleting the config file. This means the answer to "can they pick their
existing directory and have the app detect the existing `.env`?" is **yes** — via
auto-detection, an explicit folder picker, and reuse of the existing `.env`/port/projects.

---

## 5. macOS experience

### 5.1 What ships now (Phase 1 — reference implementation)

`scripts/make-macos-app.sh` builds a native-feeling **`Podocracy.app`** bundle:

```
Podocracy.app/
└── Contents/
    ├── Info.plist                     # app metadata (name, icon, high-DPI)
    ├── MacOS/Podocracy                # tiny launcher stub
    └── Resources/
        ├── podocracy-app-runtime.sh   # the GUI-aware runtime
        ├── _launch-common.sh          # copied from scripts/ (shared logic)
        └── AppIcon.icns               # generated from apps/web/icon-512.png
```

Double-clicking the app runs `scripts/podocracy-app-runtime.sh`, which:

1. **Checks Docker.** If `docker` is missing, shows a dialog offering to open the Docker
   Desktop download page (or `brew install --cask docker` when Homebrew is present),
   then quits. If Docker is installed but the daemon is not running, it runs
   `open -a Docker` and waits (with a progress notification) for the daemon.
2. **Resolves the app home.** On first run it asks which folder to use — auto-detecting an
   existing CLI setup and offering *Use this one / Choose another… / Create new* (see
   §4.2) — then remembers the choice. It downloads `docker-compose.images.yml` only when
   the folder has no compose file.
3. **Runs the first-run wizard** only when the chosen folder has no `.env`: a secure
   (hidden-answer) dialog asks for the OpenAI API key (and optionally DeepL/ElevenLabs),
   then writes `.env`. An adopted folder with an existing `.env` skips this entirely.
4. **Starts the stack** using the same `compose_up` / `wait_for_portal` functions as the
   CLI launcher, logging to `~/Podocracy/logs/launch.log`.
5. **Opens `http://localhost:8080`** in the default browser and **quits**.

Errors at any step become a friendly macOS dialog pointing at the log file.

Build it on a Mac:

```bash
./scripts/make-macos-app.sh
open ./dist/Podocracy.app
```

This is intentionally dependency-free (pure `bash` + `osascript` + `sips`/`iconutil`),
so it can be produced in CI without Electron/Tauri toolchains.

### 5.2 Gatekeeper / distribution

An unsigned `.app` triggers Gatekeeper ("cannot be opened because it is from an
unidentified developer"). Options, cheapest first:

- **Document the workaround:** right-click → **Open** once, or
  `xattr -dr com.apple.quarantine Podocracy.app`. Fine for early adopters.
- **Ad-hoc sign** (`codesign --deep -s -`): removes some warnings, still not notarized.
- **Developer ID + notarization** (requires a paid Apple Developer account): the only
  way to get a clean double-click for the general public. Ship the notarized `.app`
  inside a `.dmg` as a GitHub Release asset.

Recommendation: start with the documented right-click workaround, and add Developer ID
notarization to the release workflow once there is real distribution.

### 5.3 Phase 2 (optional, richer) — Tauri/Electron menu-bar app

If you outgrow the shell `.app`, a **menu-bar app** (Tauri preferred for size) gives:

- A persistent status icon (Starting / Running / Stopped) instead of a fire-and-quit run.
- Buttons for **Open portal**, **Stop**, **Update**, **Open projects folder**,
  **Edit keys**, **View logs**.
- A proper settings screen for API keys instead of `osascript` dialogs.
- Built-in auto-update.

It is more powerful but adds a build toolchain, code-signing complexity, and maintenance.
The shell `.app` covers 90% of the beginner benefit at ~1% of the cost, so it is the
recommended starting point.

---

## 6. Windows experience

Windows users get the same flow via `scripts/podocracy-windows-run.ps1`, a PowerShell
script that mirrors the macOS runtime using native dialogs (`MessageBox` / `InputBox`):

1. Detects Docker (`docker` on PATH + `docker info`). If missing, offers to open the
   Docker Desktop download page or run `winget install -e --id Docker.DockerDesktop`.
   If installed but not running, starts Docker Desktop and waits for the daemon.
2. Resolves the app home the same way as macOS: auto-detects an existing CLI setup, offers
   a folder picker (`FolderBrowserDialog`) or *Create new*, remembers the choice in
   `%APPDATA%\Podocracy\home.path`, and downloads the compose file only when missing.
3. Prompts for the OpenAI key with a masked input box and writes `.env` — skipped when the
   adopted folder already has one (its port/projects settings are reused).
4. Starts the stack, waits for `/api/health`, opens the default browser, and exits.

To make it feel like an app (no visible console), ship one of:

- **A `.lnk` shortcut** named "Podocracy" whose target is
  `powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File podocracy-windows-run.ps1`,
  with the icon set to `apps/web/icon-512.png` converted to `.ico`.
- **A tiny bootstrap `Podocracy.cmd`** the user can pin to the taskbar/Start menu.

For polished distribution, wrap the whole thing in an installer (Inno Setup or WiX/MSI)
that lays down the script, the shortcut, and the icon, and optionally installs Docker
Desktop as a dependency. A signed installer avoids SmartScreen warnings, mirroring the
macOS notarization story.

---

## 7. Cross-cutting: the API-key wizard

This is the highest-leverage improvement and is shared by both platforms:

- On first run only, ask for the **OpenAI API key** (required) with a link/explanation of
  where to get one; make DeepL and ElevenLabs optional and collapsible.
- Write `.env` from `.env.example` so future keys/options stay consistent.
- Never echo the key to a terminal or log; use masked input.
- Provide a **"change keys"** entry point (re-run wizard / menu item) for when a key
  rotates — otherwise beginners are stuck editing `.env` by hand again.
- Consider validating the key with a cheap OpenAI request before finishing, so users
  learn immediately if they pasted a bad key instead of after a failed job.

---

## 8. Docker installation handling

We cannot silently install Docker for the user (it needs admin rights and a large
download), but we can remove almost all the guesswork:

| Situation | macOS action | Windows action |
| --- | --- | --- |
| Docker not installed | Offer download page; if Homebrew present, offer `brew install --cask docker` | Offer download page; if winget present, offer `winget install Docker.DockerDesktop` |
| Installed, not running | `open -a Docker`, wait for daemon | Start `Docker Desktop.exe`, wait for daemon |
| Running | Proceed | Proceed |

Always show a progress indicator while waiting for the daemon (Docker Desktop can take
30–60s to become ready on a cold boot), and time out with a clear message.

A future step could bundle the Docker Desktop installer download link/version check into
the app's own installer so "install Docker" and "install Podocracy" feel like one flow.

---

## 9. Launch behavior details

- **Idempotent:** `docker compose up -d` is safe to run when containers already run, so a
  second double-click just re-opens the browser quickly.
- **First-run slowness:** show a notification like "Downloading components, this can take
  a few minutes the first time" so the long image pull / Whisper caching does not look
  like a hang.
- **Health gate before opening the browser:** keep the existing `/api/health` wait so the
  user never sees a connection-refused page.
- **Quit after launch (Phase 1):** matches the requested "open browser and quit" model.
  Containers keep running in Docker in the background (`restart: unless-stopped`).
- **Stopping:** beginners rarely need to stop containers, but provide an obvious path —
  a "Stop Podocracy" companion app/shortcut, or the menu-bar "Stop" button in Phase 2.

### 9.1 What the second launch looks like (everyday use)

The desktop app is the **single, reusable entry point** — the user keeps the same icon and
double-clicks it every time. They do **not** manually start Docker, run any container
command, or type a URL. On every launch after the first, the runtime:

1. **Skips setup.** It goes straight to the remembered folder (no folder prompt after the
   first run), and because the compose file and `.env` already exist there is no download
   and no key wizard (only the first run asks which folder to use and for the key).
2. **Starts Docker if needed.** If Docker Desktop is not running, the app starts it and
   waits for the daemon, so the user never has to open Docker themselves.
3. **Ensures containers are up.** `docker compose up -d` is idempotent: if the stack is
   already running it is a fast no-op; if it is not (e.g. after a reboot), it starts it.
   Because the compose services use `restart: unless-stopped`, they also come back on
   their own when Docker starts — the `up -d` just guarantees it.
4. **Opens the browser and quits.** It re-opens `http://localhost:8080` in the default
   browser once the health check passes.

So the everyday flow is: **double-click the icon → wait a moment → the portal opens.**
The second launch is much faster than the first because images are already pulled and
Whisper artifacts are cached.

### 9.2 Do they need a bookmark? (no)

No bookmark is required — the app opens the page for the user every time. To make it feel
like a "real app":

- **macOS:** drag `Podocracy.app` into the Dock (or keep it in `/Applications`) and click
  it whenever needed. Right-click → **Keep in Dock** to pin it.
- **Windows:** pin the launcher's `.lnk` shortcut to the Taskbar or Start menu.

A bookmark or the browser's **Install app / Add to Dock** (PWA) option is optional and
only useful as a shortcut *while the containers are already running* — importantly, the
PWA/bookmark does **not** start Docker or the containers, so it will show a
connection error if the stack is down. For that reason the launcher app (not a bookmark)
should be the primary way beginners open Podocracy; the PWA is a convenience on top.

---

## 10. Suggested roadmap

| Phase | Scope | Effort | Beginner payoff |
| --- | --- | --- | --- |
| **1 (this PR)** | Shell `.app` + PowerShell launcher, first-run key wizard, app-home folder, Docker checks | Low | Huge — removes the terminal entirely |
| **2** | Code signing / notarization (mac) + signed installer (Win) | Medium | Removes scary security warnings |
| **3** | Tauri/Electron menu-bar app with status, Stop/Update/Settings, auto-update | Medium/High | "Real app" polish, self-service updates |
| **4** | Bundle Docker install into the app installer; key validation; guided sample project | Medium | Truly zero-knowledge onboarding |

---

## 11. Summary of recommendations

- Ship the **shell-based `Podocracy.app`** and **PowerShell launcher** now (Phase 1):
  they reuse the existing, tested launcher logic and eliminate all terminal steps.
- Make the **first-run API-key wizard** the centerpiece — it removes the most error-prone
  step for beginners.
- Standardize on a **visible `~/Podocracy` app-home folder** to fix project persistence.
- Add **notarization / a signed installer** before wide distribution to avoid OS security
  warnings.
- Only invest in a **Tauri/Electron menu-bar app** once you need persistent status,
  in-app stop/update, and auto-update; the shell app covers the core need first.
