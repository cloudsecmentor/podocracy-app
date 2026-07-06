from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

PORTAL_PIPELINE_STAGES = frozenset({"transcribe", "translate", "customize", "improve", "voiceover"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def portal_enabled() -> bool:
    return not os.getenv("API_URI")


def portal_project_dir(source_path: str) -> Path | None:
    env_dir = os.getenv("PODOCRACY_PROJECT_DIR")
    if env_dir:
        return Path(env_dir)
    source = Path(source_path).resolve()
    if source.parent.name == "input":
        return source.parent.parent
    return None


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def update_portal_status(
    source_path: str,
    *,
    state: str,
    stage: str,
    progress: int,
    message: str = "",
    error: str | None = None,
) -> None:
    if not portal_enabled():
        return
    project_dir = portal_project_dir(source_path)
    if project_dir is None:
        return

    status = {
        "project_id": project_dir.name,
        "state": state,
        "stage": stage,
        "progress": progress,
        "message": message,
        "updated_at": now_iso(),
    }
    if error:
        status["error"] = error
    write_json(project_dir / "status.json", status)


def append_portal_stage(
    source_path: str,
    name: str,
    stage_status: str,
    started_at: str,
    ended_at: str,
    detail: str = "",
) -> None:
    if not portal_enabled() or name not in PORTAL_PIPELINE_STAGES:
        return
    project_dir = portal_project_dir(source_path)
    if project_dir is None:
        return

    manifest_path = project_dir / "manifest.json"
    manifest = read_json(manifest_path, {"project_id": project_dir.name, "artifacts": [], "stages": []})
    stages = manifest.setdefault("stages", [])
    stages[:] = [item for item in stages if item.get("name") != name]
    record = {
        "name": name,
        "status": stage_status,
        "started_at": started_at,
        "ended_at": ended_at,
    }
    if detail:
        record["detail"] = detail
    stages.append(record)
    write_json(manifest_path, manifest)


def requested_stages(stages_to_run: str) -> set[str]:
    if stages_to_run == "all":
        return set(PORTAL_PIPELINE_STAGES)
    return {stage for stage in stages_to_run.split("+") if stage}


def fatal_stage_failures(stage_failures: list[str], stages_to_run: str) -> list[str]:
    requested = requested_stages(stages_to_run)
    return [stage for stage in stage_failures if stage in requested]
