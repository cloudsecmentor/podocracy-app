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
DOWNLOADABLE_WORK_FILES = {
    "source.raw.json",
    "source.combined.json",
    "source.translated.json",
    "source.improved.json",
    "source.custom-instructions.json",
}

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


def parse_optional_float(value: str, field_name: str, minimum: float | None = None, maximum: float | None = None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a number") from exc
    if minimum is not None and parsed < minimum:
        raise HTTPException(status_code=400, detail=f"{field_name} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise HTTPException(status_code=400, detail=f"{field_name} must be at most {maximum}")
    return parsed


def parse_optional_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_bool(value: str, default: bool = False) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=400, detail="Boolean fields must be true or false")


def parse_tts_api(value: str) -> str:
    tts_api = (value or "openai").strip().lower()
    if tts_api not in {"openai", "elevenlabs"}:
        raise HTTPException(status_code=400, detail="tts_api must be openai or elevenlabs")
    return tts_api


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
    source: UploadFile | None = File(None),
    subtitle_file: UploadFile | None = File(None),
    custom_recordings: UploadFile | None = File(None),
    source_url: str = Form(""),
    language: str = Form("RU"),
    voice: str = Form("alloy"),
    stage_preset: str = Form("voiceover"),
    custom_instructions: str = Form(""),
    tts_api: str = Form("openai"),
    elevenlabs_voice_id: str = Form(""),
    voiceover_tempo: str = Form("1.2"),
    voiceover_shift: str = Form(""),
    normalize_final_audio: str = Form(""),
    max_preview_size_mb: str = Form("2"),
    use_subtitles_as_is: str = Form(""),
    autogenerate_custom_instructions: str = Form(""),
    detailed_transcription: str = Form("true"),
    whisper_chunk_length_sec: str = Form("300"),
    whisper_silence_split: str = Form(""),
    whisper_silence_sec: str = Form("2"),
    max_char_chunk_per_sentence: str = Form("200"),
    max_char_chunk: str = Form("700"),
    improve_max_chunk_chars: str = Form("12000"),
) -> dict[str, Any]:
    if not source_url.strip() and (source is None or not source.filename):
        raise HTTPException(status_code=400, detail="Upload a source file or provide a source URL")
    providers_present = provider_status()
    if not providers_present["openai"]:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is required")
    if not providers_present["deepl"]:
        raise HTTPException(status_code=400, detail="DEEPL_AUTH_KEY is required")

    stages_to_run = stage_mapping(stage_preset)
    parsed_tts_api = parse_tts_api(tts_api)
    if "voiceover" in stages_to_run and parsed_tts_api == "elevenlabs" and not providers_present["elevenlabs"]:
        raise HTTPException(status_code=400, detail="ELEVENLABS_API_KEY is required for ElevenLabs TTS")
    parsed_voiceover_tempo = parse_optional_float(voiceover_tempo, "voiceover_tempo", 0.5, 2.0)
    parsed_voiceover_shift = parse_optional_float(voiceover_shift, "voiceover_shift", -300.0, 300.0)
    parsed_max_preview_size_mb = parse_optional_float(max_preview_size_mb, "max_preview_size_mb", 0.1, 500.0)
    parsed_whisper_chunk_length_sec = parse_optional_float(whisper_chunk_length_sec, "whisper_chunk_length_sec", 10.0, 3600.0)
    parsed_whisper_silence_sec = parse_optional_float(whisper_silence_sec, "whisper_silence_sec", 0.1, 30.0)
    parsed_max_char_chunk_per_sentence = parse_optional_float(max_char_chunk_per_sentence, "max_char_chunk_per_sentence", 20.0, 5000.0)
    parsed_max_char_chunk = parse_optional_float(max_char_chunk, "max_char_chunk", 50.0, 20000.0)
    parsed_improve_max_chunk_chars = parse_optional_float(improve_max_chunk_chars, "improve_max_chunk_chars", 500.0, 200000.0)

    project_id = f"project-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root = PROJECTS_DIR / project_id
    input_dir = root / "input"
    output_dir = root / "output"
    logs_dir = root / "logs"
    config_dir = root / "config"
    work_dir = root / "work"
    for directory in (input_dir, output_dir, logs_dir, config_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if source_url.strip():
        filename = "source.url"
    else:
        assert source is not None
        filename = safe_name(source.filename or "source.mp3")
    source_path = input_dir / filename
    if source_url.strip():
        write_json(source_path, {"url": source_url.strip()})
    else:
        assert source is not None
        with source_path.open("wb") as handle:
            shutil.copyfileobj(source.file, handle)

    subtitle_relative = ""
    if subtitle_file and subtitle_file.filename:
        subtitle_name = f"subtitles{Path(safe_name(subtitle_file.filename)).suffix.lower() or '.srt'}"
        subtitle_path = input_dir / subtitle_name
        with subtitle_path.open("wb") as handle:
            shutil.copyfileobj(subtitle_file.file, handle)
        subtitle_relative = f"input/{subtitle_name}"

    custom_recordings_relative = ""
    if custom_recordings and custom_recordings.filename:
        recordings_name = "custom-recordings.zip"
        recordings_path = input_dir / recordings_name
        with recordings_path.open("wb") as handle:
            shutil.copyfileobj(custom_recordings.file, handle)
        custom_recordings_relative = f"input/{recordings_name}"

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
        "tts_api": parsed_tts_api,
        "translation_text_key": "translated_text",
        "improved_text_key": "voiceover_text",
        "speedup_value": 1.2,
        "normalize_final_audio": parse_optional_bool(normalize_final_audio),
        "use_subtitles_as_is": parse_optional_bool(use_subtitles_as_is),
        "autogenerate_custom_instructions": parse_optional_bool(autogenerate_custom_instructions),
        "detailed_transcription": parse_bool(detailed_transcription, default=True),
        "whisper_chunk_length_sec": int(parsed_whisper_chunk_length_sec or 300),
        "whisper_silence_split": parse_optional_bool(whisper_silence_split),
        "whisper_silence_sec": parsed_whisper_silence_sec if parsed_whisper_silence_sec is not None else 2,
        "max_char_chunk_per_sentence": int(parsed_max_char_chunk_per_sentence or 200),
        "max_char_chunk": int(parsed_max_char_chunk or 700),
        "improve_max_chunk_chars": int(parsed_improve_max_chunk_chars or 12000),
        "max_preview_size_mb": parsed_max_preview_size_mb if parsed_max_preview_size_mb is not None else 2.0,
    }
    if parsed_voiceover_tempo is not None:
        params["voiceover_tempo"] = parsed_voiceover_tempo
    if parsed_voiceover_shift is not None:
        params["voiceover_shift"] = parsed_voiceover_shift
    if elevenlabs_voice_id.strip():
        params["elevenlabs_voice_id"] = elevenlabs_voice_id.strip()
    if subtitle_relative:
        params["custom_subtitles"] = subtitle_relative
    if custom_recordings_relative:
        params["custom_recording"] = True
        params["custom_recordings_zip"] = custom_recordings_relative
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
        "tts_api": parsed_tts_api,
        "voiceover_tempo": parsed_voiceover_tempo if parsed_voiceover_tempo is not None else 1.2,
        "voiceover_shift": parsed_voiceover_shift if parsed_voiceover_shift is not None else 0,
        "custom_subtitles": bool(subtitle_relative),
        "custom_recording": bool(custom_recordings_relative),
        "normalize_final_audio": parse_optional_bool(normalize_final_audio),
        "autogenerate_custom_instructions": parse_optional_bool(autogenerate_custom_instructions),
        "detailed_transcription": parse_bool(detailed_transcription, default=True),
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
    for path in sorted((root / "work").glob("source.*.json")):
        if path.is_file() and path.name in DOWNLOADABLE_WORK_FILES:
            artifacts.append(
                {
                    "name": path.name,
                    "path": f"work/{path.name}",
                    "bytes": path.stat().st_size,
                    "download_url": f"/api/projects/{project_id}/download/{path.name}",
                }
            )
    return artifacts


@app.get("/api/projects/{project_id}/download/{filename}")
def download_artifact(project_id: str, filename: str) -> FileResponse:
    root = project_path(project_id)
    safe_filename = safe_name(filename)
    artifact = root / "output" / safe_filename
    if not artifact.exists() and safe_filename in DOWNLOADABLE_WORK_FILES:
        artifact = root / "work" / safe_filename
    if not artifact.exists() or not artifact.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path=artifact, filename=artifact.name)


@app.get("/api/projects/{project_id}/support-bundle")
def support_bundle(project_id: str) -> FileResponse:
    root = project_path(project_id)
    bundle_path = root / "output" / f"{project_id}.support.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative in [
            "metadata.json",
            "status.json",
            "manifest.json",
            "config/params.json",
            "work/source.improved.json",
            "work/source.custom-instructions.json",
        ]:
            path = root / relative
            if path.exists():
                bundle.write(path, relative)
        for log_path in (root / "logs").glob("*.log"):
            bundle.write(log_path, f"logs/{log_path.name}")
    return FileResponse(path=bundle_path, filename=bundle_path.name)
