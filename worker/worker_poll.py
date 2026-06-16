from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/data/projects"))
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "3"))
WORKER_ROOT = Path(__file__).resolve().parent
PROCESSING_DIR = WORKER_ROOT / "processing_container"
LEGACY_PROCESSING_DIR = WORKER_ROOT / "backend" / "processing_container"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def update_status(project: Path, state: str, stage: str, progress: int, message: str = "", error: str | None = None) -> None:
    status = {
        "project_id": project.name,
        "state": state,
        "stage": stage,
        "progress": progress,
        "message": message,
        "updated_at": now_iso(),
    }
    if error:
        status["error"] = error
    write_json(project / "status.json", status)


def ensure_legacy_layout() -> None:
    LEGACY_PROCESSING_DIR.parent.mkdir(parents=True, exist_ok=True)
    if LEGACY_PROCESSING_DIR.exists():
        return
    try:
        LEGACY_PROCESSING_DIR.symlink_to(PROCESSING_DIR, target_is_directory=True)
    except OSError:
        import shutil

        shutil.copytree(PROCESSING_DIR, LEGACY_PROCESSING_DIR)


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


def process_project(project: Path) -> None:
    ensure_legacy_layout()
    source_path = project_source_path(project)
    params_path = source_path.with_suffix(".params.json")
    if not params_path.exists():
        raise FileNotFoundError(f"Legacy params file is missing: {params_path}")

    logs_dir = project / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "orchestrator.log"
    command = [sys.executable, str(PROCESSING_DIR / "pd-00-orchestrator.py"), "-p", str(source_path)]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{WORKER_ROOT}{os.pathsep}{PROCESSING_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"

    update_status(project, "running", "legacy-worker", 1, "Legacy worker started")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{now_iso()}] Running {' '.join(command)}\n")
        log.flush()
        result = subprocess.run(
            command,
            cwd=str(WORKER_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

    if result.returncode != 0:
        update_status(
            project,
            "failed",
            "legacy-worker",
            100,
            "Legacy worker failed",
            error=f"orchestrator exited with {result.returncode}",
        )
        return

    update_status(project, "completed", "completed", 100, "Legacy worker completed")


def acquire_lock(project: Path) -> int | None:
    lock_path = project / ".worker.lock"
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def release_lock(project: Path, fd: int | None) -> None:
    if fd is not None:
        os.close(fd)
    lock_path = project / ".worker.lock"
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def next_queued_project() -> Path | None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    for project in sorted(PROJECTS_DIR.glob("project-*")):
        status = read_json(project / "status.json", {})
        if status.get("state") == "queued":
            return project
    return None


def main() -> None:
    ensure_legacy_layout()
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
                update_status(project, "failed", "legacy-worker", 100, "Legacy worker failed", error=str(exc))
                raise
        finally:
            release_lock(project, fd)


if __name__ == "__main__":
    main()
