from __future__ import annotations

import os
import time
from pathlib import Path

from local_worker import process_project, read_json


PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/data/projects"))
POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "3"))


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
            process_project(project)
        finally:
            release_lock(project, fd)


if __name__ == "__main__":
    main()
