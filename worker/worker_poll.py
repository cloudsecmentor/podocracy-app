from __future__ import annotations

import json
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/data/projects"))
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "3"))
WORKER_ROOT = Path(__file__).resolve().parent
PROCESSING_DIR = WORKER_ROOT / "processing_container"
ORCHESTRATOR_LAYOUT_DIR = WORKER_ROOT / "backend" / "processing_container"

# The portal cannot signal this container's processes, so a stop arrives as a
# file on the shared projects volume and is picked up by the wait loop below.
CANCEL_REQUEST_NAME = "cancel.request"
HEARTBEAT_SECONDS = float(os.getenv("WORKER_HEARTBEAT_SECONDS", "10"))
# How long a stage gets to exit on SIGTERM before the group is killed outright.
CANCEL_GRACE_SECONDS = float(os.getenv("WORKER_CANCEL_GRACE_SECONDS", "10"))
# Lets the portal tell "this run is mine" from "this run belongs to a worker
# that no longer exists".
WORKER_BOOT_ID = uuid.uuid4().hex
LIVE_STATES = {"running", "cancelling"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        # status.json has three writers. Losing one read is survivable; raising
        # here used to take the whole worker down with it.
        return default


def write_json(path: Path, data) -> None:
    """Atomic, because the portal and the pipeline read these files while the
    worker writes them. Truncating in place is briefly visible as an empty file,
    which readers see as invalid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def update_status(
    project: Path,
    state: str,
    stage: str,
    progress: int,
    message: str = "",
    error: str | None = None,
    alive: bool = False,
) -> None:
    previous = read_json(project / "status.json", {})
    status = {
        "project_id": project.name,
        "state": state,
        "stage": stage,
        "progress": progress,
        "message": message,
        "updated_at": now_iso(),
    }
    # The portal sets job_kind when it queues the work; carrying it through the
    # run is what lets the UI say "regenerating one chunk" rather than "running".
    if previous.get("job_kind"):
        status["job_kind"] = previous["job_kind"]
    if alive:
        status["heartbeat"] = now_iso()
        status["worker_boot_id"] = WORKER_BOOT_ID
    if error:
        status["error"] = error
    write_json(project / "status.json", status)


def cancel_request_path(project: Path) -> Path:
    return project / CANCEL_REQUEST_NAME


def cancel_requested(project: Path) -> bool:
    return cancel_request_path(project).exists()


def clear_cancel_request(project: Path) -> None:
    cancel_request_path(project).unlink(missing_ok=True)


def beat(project: Path) -> None:
    """Refresh liveness without disturbing the stage the orchestrator reported."""
    status = read_json(project / "status.json", {})
    if status.get("state") not in LIVE_STATES:
        return
    status["heartbeat"] = now_iso()
    status["worker_boot_id"] = WORKER_BOOT_ID
    write_json(project / "status.json", status)


def terminate_process_group(process: subprocess.Popen, log) -> None:
    """Kill the whole stage subtree, not just the orchestrator.

    The orchestrator shells out per stage and those stages shell out to ffmpeg,
    so signalling the direct child alone would leave grandchildren running and
    still writing into the project.
    """
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, OSError):
        return
    for name, sig in (("SIGTERM", signal.SIGTERM), ("SIGKILL", signal.SIGKILL)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        log.write(f"[{now_iso()}] Sent {name} to process group {pgid}\n")
        log.flush()
        try:
            process.wait(timeout=CANCEL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def ensure_orchestrator_layout() -> None:
    ORCHESTRATOR_LAYOUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    if ORCHESTRATOR_LAYOUT_DIR.exists():
        return
    try:
        ORCHESTRATOR_LAYOUT_DIR.symlink_to(PROCESSING_DIR, target_is_directory=True)
    except OSError:
        shutil.copytree(PROCESSING_DIR, ORCHESTRATOR_LAYOUT_DIR)


def project_source_path(project: Path) -> Path:
    metadata = read_json(project / "metadata.json", {})
    relative = metadata.get("source_path")
    if relative:
        source = project / relative
        if source.exists():
            return source
    candidates = [
        item
        for item in (project / "input").glob("*")
        if item.is_file() and not item.name.endswith(".json") and ".subtitles." not in item.name
    ]
    if not candidates:
        raise FileNotFoundError("No source file found in project input folder")
    return candidates[0]


def run_with_cancel(project: Path, command: list[str], env: dict, log_path: Path) -> tuple[str, int | None]:
    """Run the pipeline, watching for a stop request and keeping liveness fresh.

    Returns ("cancelled" | "finished", returncode). Blocking on the child the
    way this used to would make a stop impossible to observe.
    """
    cancelled = False
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now_iso()}] Running {' '.join(command)}\n")
        log.flush()
        process = subprocess.Popen(
            command,
            cwd=str(WORKER_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            # Own process group, so the subtree can be signalled without this
            # worker signalling itself.
            start_new_session=True,
        )
        last_beat = time.monotonic()
        while True:
            try:
                process.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pass
            if not cancelled and cancel_requested(project):
                cancelled = True
                log.write(f"[{now_iso()}] Stop requested, terminating the run\n")
                log.flush()
                update_status(
                    project, "cancelling", "cancelling",
                    read_json(project / "status.json", {}).get("progress") or 0,
                    "Stopping", alive=True,
                )
                terminate_process_group(process, log)
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                beat(project)
                last_beat = time.monotonic()

    return ("cancelled" if cancelled else "finished"), process.returncode


def process_project(project: Path) -> None:
    ensure_orchestrator_layout()
    source_path = project_source_path(project)
    # config/params.json is the source of truth; the pipeline only ever reads the input-dir copy,
    # which stages such as customize rewrite mid-run, so refresh it from config before each run.
    params_path = source_path.with_suffix(".params.json")
    config_params_path = project / "config" / "params.json"
    if config_params_path.exists():
        shutil.copyfile(config_params_path, params_path)
    elif not params_path.exists():
        raise FileNotFoundError(f"Params file is missing: {config_params_path}")

    logs_dir = project / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "orchestrator.log"
    command = [sys.executable, str(PROCESSING_DIR / "pd-00-orchestrator.py"), "-p", str(source_path)]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{WORKER_ROOT}{os.pathsep}{PROCESSING_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["PODOCRACY_PROJECT_DIR"] = str(project)

    # A stop that landed while this was still queued must not be spent on the run.
    if cancel_requested(project):
        clear_cancel_request(project)
        update_status(project, "cancelled", "cancelled", 0, "Stopped before processing started")
        return
    clear_cancel_request(project)

    update_status(project, "running", "starting", 1, "Worker started", alive=True)
    outcome, returncode = run_with_cancel(project, command, env, log_path)

    clear_cancel_request(project)

    if outcome == "cancelled":
        update_status(project, "cancelled", "cancelled", 0, "Stopped on request")
        return

    status = read_json(project / "status.json", {})
    if status.get("state") in {"completed", "failed"}:
        return

    if returncode != 0:
        update_status(
            project,
            "failed",
            status.get("stage") or "failed",
            100,
            "Worker failed",
            error=f"orchestrator exited with {returncode}",
        )
        return

    update_status(project, "completed", "completed", 100, "Project completed")


def acquire_lock(project: Path) -> int | None:
    lock_path = project / ".worker.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def release_lock(project: Path, fd: int | None) -> None:
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def sweep_interrupted_runs() -> None:
    """Clear runs orphaned by a worker restart.

    A run cannot outlive the worker that spawned it, so a project still marked
    running at startup was interrupted. Without this, the portal keeps refusing
    new work on that project because it looks busy forever.
    """
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    for project in sorted(PROJECTS_DIR.glob("project-*")):
        status = read_json(project / "status.json", {})
        if status.get("state") not in LIVE_STATES:
            continue
        # If the lock is held, another worker really is processing this.
        fd = acquire_lock(project)
        if fd is None:
            continue
        try:
            clear_cancel_request(project)
            update_status(
                project,
                "interrupted",
                status.get("stage") or "unknown",
                status.get("progress") or 0,
                "Interrupted when the worker restarted; start it again when ready",
                error="worker restarted while this project was processing",
            )
            print(f"Reset interrupted project {project.name}", flush=True)
        finally:
            release_lock(project, fd)


def next_queued_project() -> Path | None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    for project in sorted(PROJECTS_DIR.glob("project-*")):
        status = read_json(project / "status.json", {})
        if status.get("state") == "queued":
            return project
    return None


def main() -> None:
    ensure_orchestrator_layout()
    sweep_interrupted_runs()
    print(f"Worker polling {PROJECTS_DIR}", flush=True)
    while True:
        project = next_queued_project()
        if project is None:
            time.sleep(POLL_SECONDS)
            continue

        fd = acquire_lock(project)
        if fd is None:
            time.sleep(POLL_SECONDS)
            continue

        try:
            try:
                process_project(project)
            except Exception as exc:
                # Keep polling. Exiting here takes the worker down, and the
                # restart policy brings it back with every other project's
                # status frozen mid-run.
                traceback.print_exc()
                update_status(project, "failed", "failed", 100, "Worker failed", error=str(exc))
                clear_cancel_request(project)
        finally:
            release_lock(project, fd)


if __name__ == "__main__":
    main()
