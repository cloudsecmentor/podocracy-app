from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse


PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/data/projects"))
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Podocracy Worker Portal API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(name: str) -> str:
    keep = []
    for char in Path(name).name:
        if char.isalnum() or char in {".", "-", "_"}:
            keep.append(char)
        else:
            keep.append("-")
    cleaned = "".join(keep).strip(".-")
    return cleaned or "source.mp3"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def project_path(project_id: str) -> Path:
    if not project_id.startswith("project-"):
        raise HTTPException(status_code=404, detail="Project not found")
    path = PROJECTS_DIR / project_id
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return path


def project_summary(path: Path) -> dict[str, Any]:
    metadata = read_json(path / "metadata.json", {})
    status = read_json(path / "status.json", {})
    manifest = read_json(path / "manifest.json", {})
    return {
        "id": path.name,
        "metadata": metadata,
        "status": status,
        "manifest": manifest,
    }


def stage_mapping(stage_preset: str) -> str:
    if stage_preset == "full":
        return "transcribe+translate+improve+voiceover"
    if stage_preset == "translate-only":
        return "transcribe+translate"
    return "transcribe+translate+voiceover"


def provider_status() -> dict[str, bool]:
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "deepl": bool(os.getenv("DEEPL_AUTH_KEY")),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "projects_dir": str(PROJECTS_DIR), "providers": provider_status()}


@app.get("/api/providers")
def providers() -> dict[str, bool]:
    return provider_status()


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    paths = sorted(PROJECTS_DIR.glob("project-*"), key=lambda item: item.name, reverse=True)
    return [project_summary(path) for path in paths if path.is_dir()]


@app.post("/api/projects")
def create_project(
    source: UploadFile = File(...),
    language: str = Form("RU"),
    voice: str = Form("alloy"),
    stage_preset: str = Form("voiceover"),
    custom_instructions: str = Form(""),
) -> dict[str, Any]:
    if not source.filename:
        raise HTTPException(status_code=400, detail="Missing source filename")
    providers_present = provider_status()
    if not providers_present["openai"]:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is required")
    if not providers_present["deepl"]:
        raise HTTPException(status_code=400, detail="DEEPL_AUTH_KEY is required")

    project_id = f"project-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root = PROJECTS_DIR / project_id
    input_dir = root / "input"
    output_dir = root / "output"
    logs_dir = root / "logs"
    config_dir = root / "config"
    work_dir = root / "work"
    for directory in (input_dir, output_dir, logs_dir, config_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=True)

    filename = safe_name(source.filename)
    source_path = input_dir / filename
    with source_path.open("wb") as handle:
        shutil.copyfileobj(source.file, handle)

    stages_to_run = stage_mapping(stage_preset)
    params = {
        "schema_version": "local-worker-v1",
        "user_id": "local",
        "filename": filename,
        "language": language,
        "target_language": language,
        "voice": voice,
        "custom_instructions": custom_instructions,
        "stage_preset": stage_preset,
        "stages_to_run": stages_to_run,
        "whisper_api": True,
        "tts_api": "openai",
        "translation_text_key": "translated_text",
        "improved_text_key": "voiceover_text",
    }
    write_json(config_dir / "params.json", params)
    write_json(source_path.with_suffix(".params.json"), params)

    metadata = {
        "id": project_id,
        "created_at": now_iso(),
        "source_filename": filename,
        "source_path": f"input/{filename}",
        "target_language": language,
        "voice": voice,
        "stage_preset": stage_preset,
    }
    status = {
        "project_id": project_id,
        "state": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Waiting for worker",
        "updated_at": now_iso(),
    }
    write_json(root / "metadata.json", metadata)
    write_json(root / "status.json", status)
    write_json(root / "manifest.json", {"project_id": project_id, "artifacts": [], "stages": []})

    return project_summary(root)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return project_summary(project_path(project_id))


@app.get("/api/projects/{project_id}/logs", response_class=PlainTextResponse)
def get_project_logs(project_id: str) -> str:
    root = project_path(project_id)
    logs = []
    for log_path in sorted((root / "logs").glob("*.log")):
        logs.append(f"===== {log_path.name} =====\n")
        logs.append(log_path.read_text(encoding="utf-8", errors="replace")[-12000:])
        logs.append("\n")
    return "".join(logs) or "No logs yet.\n"


@app.get("/api/projects/{project_id}/artifacts")
def get_artifacts(project_id: str) -> list[dict[str, Any]]:
    root = project_path(project_id)
    artifacts = []
    for path in sorted((root / "output").glob("*")):
        if path.is_file():
            artifacts.append(
                {
                    "name": path.name,
                    "path": f"output/{path.name}",
                    "bytes": path.stat().st_size,
                    "download_url": f"/api/projects/{project_id}/download/{path.name}",
                }
            )
    return artifacts


@app.get("/api/projects/{project_id}/download/{filename}")
def download_artifact(project_id: str, filename: str) -> FileResponse:
    root = project_path(project_id)
    artifact = root / "output" / safe_name(filename)
    if not artifact.exists() or not artifact.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path=artifact, filename=artifact.name)


@app.get("/api/projects/{project_id}/support-bundle")
def support_bundle(project_id: str) -> FileResponse:
    root = project_path(project_id)
    bundle_path = root / "output" / f"{project_id}.support.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative in ["metadata.json", "status.json", "manifest.json", "config/params.json"]:
            path = root / relative
            if path.exists():
                bundle.write(path, relative)
        for log_path in (root / "logs").glob("*.log"):
            bundle.write(log_path, f"logs/{log_path.name}")
    return FileResponse(path=bundle_path, filename=bundle_path.name)
