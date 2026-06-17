from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

import requests


PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/data/projects"))
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADABLE_WORK_FILES = {
    "source.raw.json",
    "source.combined.json",
    "source.translated.json",
    "source.improved.json",
    "source.custom-instructions.json",
    "source.custom-instructions.txt",
}
STAGE_OPTIONS = {"transcribe", "translate", "customize", "improve", "voiceover"}
SUPPORTED_TARGET_LANGUAGES = {
    "EN": "English",
    "RU": "Russian",
    "UK": "Ukrainian",
    "JA": "Japanese",
    "ZH": "Chinese",
    "ES": "Spanish",
    "FR": "French",
    "DE": "German",
    "IT": "Italian",
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


def source_path_for_project(root: Path) -> Path | None:
    metadata = read_json(root / "metadata.json", {})
    relative = metadata.get("source_path")
    if relative:
        source = root / relative
        if source.exists():
            return source
    candidates = [item for item in (root / "input").glob("*") if item.is_file() and not item.name.endswith(".json")]
    return candidates[0] if candidates else None


def legacy_artifact_paths(root: Path) -> list[Path]:
    source_path = source_path_for_project(root)
    if source_path is None:
        return []
    excluded = {
        source_path.name,
        source_path.with_suffix(".params.json").name,
    }
    artifacts = []
    for path in sorted(source_path.parent.glob(f"{source_path.stem}.*")):
        if path.is_file() and path.name not in excluded and ".subtitles." not in path.name:
            artifacts.append(path)
    return artifacts


def is_improved_filename(filename: str) -> bool:
    return filename.endswith(".improved.json")


def improved_artifact_for_project(root: Path) -> Path | None:
    candidates = []
    for directory in (root / "work", root / "output", root / "input"):
        candidates.extend(
            path
            for path in directory.glob("*.improved.json")
            if path.is_file()
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (0 if item.parent.name == "work" else 1, item.name))
    return candidates[0]


def stage_mapping(stage_preset: str) -> str:
    if stage_preset == "full":
        return "transcribe+combine+timesync+translate+customize+improve+voiceover"
    if stage_preset == "translate-only":
        return "transcribe+combine+timesync+translate"
    return "transcribe+combine+timesync+translate+customize+voiceover"


def normalize_stage_list(stage_list: str, fallback_preset: str) -> str:
    parts = [part.strip().lower() for part in stage_list.split("+") if part.strip()]
    filtered = [part for part in parts if part in STAGE_OPTIONS]
    if filtered:
        return "+".join(dict.fromkeys(filtered))
    return stage_mapping(fallback_preset)


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


def parse_translation_provider(value: str) -> str:
    provider = (value or "openai").strip().lower()
    if provider not in {"openai", "deepl"}:
        raise HTTPException(status_code=400, detail="translation_provider must be openai or deepl")
    return provider


def parse_target_language(value: str) -> str:
    language = (value or "EN").strip().upper()
    if language not in SUPPORTED_TARGET_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_TARGET_LANGUAGES))
        raise HTTPException(status_code=400, detail=f"language must be one of: {supported}")
    return language


def bema_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def strip_html_to_text(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def download_bema_episode(episode: int) -> tuple[str, bytes, str]:
    bema_url = f"https://www.bemadiscipleship.com/{episode}"
    try:
        page_response = requests.get(bema_url, headers=bema_headers(), timeout=30)
        page_response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch BEMA episode: {exc}") from exc

    page_html = page_response.text
    mp3_match = re.search(r"https://aphid\.fireside\.fm[^\"'\s]+\.mp3", page_html, flags=re.IGNORECASE)
    if not mp3_match:
        raise HTTPException(status_code=404, detail="BEMA episode MP3 link not found")

    mp3_url = mp3_match.group(0)
    mp3_filename = f"e{episode:03d}.mp3"
    try:
        mp3_response = requests.get(mp3_url, headers=bema_headers(), timeout=120)
        mp3_response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Failed to download BEMA MP3: {exc}") from exc

    transcript_text = ""
    transcript_link_match = re.search(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*Transcript for',
        page_html,
        flags=re.IGNORECASE,
    )
    if transcript_link_match:
        transcript_url = transcript_link_match.group(1)
        try:
            transcript_response = requests.get(transcript_url, headers=bema_headers(), timeout=30)
            transcript_response.raise_for_status()
            transcript_text = strip_html_to_text(transcript_response.text)
        except requests.RequestException:
            transcript_text = ""

    return mp3_filename, mp3_response.content, transcript_text


def create_project_root() -> tuple[str, Path, Path, Path, Path, Path, Path]:
    project_id = f"project-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root = PROJECTS_DIR / project_id
    input_dir = root / "input"
    output_dir = root / "output"
    logs_dir = root / "logs"
    config_dir = root / "config"
    work_dir = root / "work"
    for directory in (input_dir, output_dir, logs_dir, config_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return project_id, root, input_dir, output_dir, logs_dir, config_dir, work_dir


def build_project_payload(
    *,
    filename: str,
    language: str,
    voice: str,
    stage_preset: str,
    stages_to_run: str = "",
    custom_instructions: str,
    tts_api: str,
    translation_provider: str = "openai",
    elevenlabs_voice_id: str = "",
    voiceover_tempo: float | None = None,
    voiceover_shift: float | None = None,
    normalize_final_audio: bool = False,
    max_preview_size_mb: float = 2.0,
    use_subtitles_as_is: bool = False,
    autogenerate_custom_instructions: bool = False,
    detailed_transcription: bool = True,
    whisper_chunk_length_sec: int = 300,
    whisper_silence_split: bool = False,
    whisper_silence_sec: float = 2.0,
    max_char_chunk_per_sentence: int = 200,
    max_char_chunk: int = 400,
    improve_max_chunk_chars: int = 12000,
    subtitle_relative: str = "",
    custom_recordings_relative: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed_tts_api = parse_tts_api(tts_api)
    parsed_language = parse_target_language(language)
    parsed_translation_provider = parse_translation_provider(translation_provider)
    resolved_stages = normalize_stage_list(stages_to_run, stage_preset)
    params = {
        "schema_version": "local-worker-v1",
        "user_id": "local",
        "filename": filename,
        "language": parsed_language,
        "target_language": parsed_language,
        "voice": voice,
        "custom_instructions": custom_instructions,
        "stage_preset": stage_preset,
        "stages_to_run": resolved_stages,
        "whisper_api": True,
        "tts_api": parsed_tts_api,
        "translation_provider": parsed_translation_provider,
        "translation_text_key": "dltrans",
        "improved_text_key": "imp",
        "speedup_value": 1.2,
        "normalize_final_audio": normalize_final_audio,
        "use_subtitles_as_is": use_subtitles_as_is,
        "autogenerate_custom_instructions": autogenerate_custom_instructions,
        "detailed_transcription": detailed_transcription,
        "whisper_chunk_length_sec": whisper_chunk_length_sec,
        "whisper_silence_split": whisper_silence_split,
        "whisper_silence_sec": whisper_silence_sec,
        "max_char_chunk_per_sentence": max_char_chunk_per_sentence,
        "max_char_chunk": max_char_chunk,
        "improve_max_chunk_chars": improve_max_chunk_chars,
        "max_preview_size_mb": max_preview_size_mb,
        "max_video_file_size_mb": max_preview_size_mb,
    }
    if voiceover_tempo is not None:
        params["voiceover_tempo"] = voiceover_tempo
    if voiceover_shift is not None:
        params["voiceover_shift"] = voiceover_shift
    if elevenlabs_voice_id.strip():
        params["elevenlabs_voice_id"] = elevenlabs_voice_id.strip()
    if subtitle_relative:
        params["custom_subtitles"] = "true"
        params["custom_subtitles_path"] = subtitle_relative
    if custom_recordings_relative:
        params["custom_recording"] = True
        params["custom_recordings_zip"] = custom_recordings_relative

    metadata = {
        "created_at": now_iso(),
        "source_filename": filename,
        "source_path": f"input/{filename}",
        "target_language": parsed_language,
        "voice": voice,
        "stage_preset": stage_preset,
        "stages_to_run": resolved_stages,
        "tts_api": parsed_tts_api,
        "translation_provider": parsed_translation_provider,
        "voiceover_tempo": voiceover_tempo if voiceover_tempo is not None else 1.2,
        "voiceover_shift": voiceover_shift if voiceover_shift is not None else 1.5,
        "custom_subtitles": bool(subtitle_relative),
        "custom_recording": bool(custom_recordings_relative),
        "normalize_final_audio": normalize_final_audio,
        "autogenerate_custom_instructions": autogenerate_custom_instructions,
        "detailed_transcription": detailed_transcription,
    }
    return params, metadata


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
    language: str = Form("EN"),
    voice: str = Form("alloy"),
    stage_preset: str = Form("voiceover"),
    stages_to_run: str = Form(""),
    custom_instructions: str = Form(""),
    tts_api: str = Form("openai"),
    translation_provider: str = Form("openai"),
    elevenlabs_voice_id: str = Form(""),
    voiceover_tempo: str = Form("1.2"),
    voiceover_shift: str = Form("1.5"),
    normalize_final_audio: str = Form(""),
    max_preview_size_mb: str = Form("2"),
    use_subtitles_as_is: str = Form(""),
    autogenerate_custom_instructions: str = Form(""),
    detailed_transcription: str = Form("true"),
    whisper_chunk_length_sec: str = Form("300"),
    whisper_silence_split: str = Form(""),
    whisper_silence_sec: str = Form("2"),
    max_char_chunk_per_sentence: str = Form("200"),
    max_char_chunk: str = Form("400"),
    improve_max_chunk_chars: str = Form("12000"),
) -> dict[str, Any]:
    if not source_url.strip() and (source is None or not source.filename):
        raise HTTPException(status_code=400, detail="Upload a source file or provide a source URL")
    providers_present = provider_status()
    if not providers_present["openai"]:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is required")

    parsed_language = parse_target_language(language)
    parsed_translation_provider = parse_translation_provider(translation_provider)
    if parsed_translation_provider == "deepl" and not providers_present["deepl"]:
        raise HTTPException(status_code=400, detail="DEEPL_AUTH_KEY is required for DeepL translation")
    resolved_stages_to_run = normalize_stage_list(stages_to_run, stage_preset)
    parsed_tts_api = parse_tts_api(tts_api)
    if "voiceover" in resolved_stages_to_run and parsed_tts_api == "elevenlabs" and not providers_present["elevenlabs"]:
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
        subtitle_name = f"{source_path.stem}.subtitles{Path(safe_name(subtitle_file.filename)).suffix.lower() or '.srt'}"
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
        "language": parsed_language,
        "target_language": parsed_language,
        "voice": voice,
        "custom_instructions": custom_instructions,
        "stage_preset": stage_preset,
        "stages_to_run": resolved_stages_to_run,
        "whisper_api": True,
        "tts_api": parsed_tts_api,
        "translation_provider": parsed_translation_provider,
        "translation_text_key": "dltrans",
        "improved_text_key": "imp",
        "speedup_value": 1.2,
        "normalize_final_audio": parse_optional_bool(normalize_final_audio),
        "use_subtitles_as_is": parse_optional_bool(use_subtitles_as_is),
        "autogenerate_custom_instructions": parse_optional_bool(autogenerate_custom_instructions),
        "detailed_transcription": parse_bool(detailed_transcription, default=True),
        "whisper_chunk_length_sec": int(parsed_whisper_chunk_length_sec or 300),
        "whisper_silence_split": parse_optional_bool(whisper_silence_split),
        "whisper_silence_sec": parsed_whisper_silence_sec if parsed_whisper_silence_sec is not None else 2,
        "max_char_chunk_per_sentence": int(parsed_max_char_chunk_per_sentence or 200),
        "max_char_chunk": int(parsed_max_char_chunk or 400),
        "improve_max_chunk_chars": int(parsed_improve_max_chunk_chars or 12000),
        "max_preview_size_mb": parsed_max_preview_size_mb if parsed_max_preview_size_mb is not None else 2.0,
        "max_video_file_size_mb": parsed_max_preview_size_mb if parsed_max_preview_size_mb is not None else 2.0,
    }
    if parsed_voiceover_tempo is not None:
        params["voiceover_tempo"] = parsed_voiceover_tempo
    if parsed_voiceover_shift is not None:
        params["voiceover_shift"] = parsed_voiceover_shift
    if elevenlabs_voice_id.strip():
        params["elevenlabs_voice_id"] = elevenlabs_voice_id.strip()
    if subtitle_relative:
        params["custom_subtitles"] = "true"
        params["custom_subtitles_path"] = subtitle_relative
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
        "target_language": parsed_language,
        "voice": voice,
        "stage_preset": stage_preset,
        "stages_to_run": resolved_stages_to_run,
        "tts_api": parsed_tts_api,
        "translation_provider": parsed_translation_provider,
        "voiceover_tempo": parsed_voiceover_tempo if parsed_voiceover_tempo is not None else 1.2,
        "voiceover_shift": parsed_voiceover_shift if parsed_voiceover_shift is not None else 1.5,
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


class ImportBemaEpisodeRequest(BaseModel):
    episode: int
    include_transcript: bool = True


class FileContentUpdateRequest(BaseModel):
    content: str


@app.post("/api/projects/bema")
def import_bema_episode(body: ImportBemaEpisodeRequest) -> dict[str, Any]:
    if body.episode <= 0 or body.episode > 9999:
        raise HTTPException(status_code=400, detail="episode must be between 1 and 9999")

    mp3_filename, mp3_content, transcript_text = download_bema_episode(body.episode)
    project_id, root, input_dir, output_dir, logs_dir, config_dir, work_dir = create_project_root()
    source_path = input_dir / mp3_filename
    source_path.write_bytes(mp3_content)

    transcript_filename = ""
    if body.include_transcript and transcript_text:
        transcript_filename = f"{source_path.stem}.proofread.txt"
        transcript_path = input_dir / transcript_filename
        transcript_path.write_text(transcript_text, encoding="utf-8")

    params, metadata = build_project_payload(
        filename=mp3_filename,
        language="EN",
        voice="alloy",
        stage_preset="voiceover",
        custom_instructions="",
        tts_api="openai",
        voiceover_tempo=1.2,
        voiceover_shift=1.5,
        normalize_final_audio=False,
        max_preview_size_mb=2.0,
        use_subtitles_as_is=False,
        autogenerate_custom_instructions=False,
        detailed_transcription=True,
        whisper_chunk_length_sec=300,
        whisper_silence_split=False,
        whisper_silence_sec=2.0,
        max_char_chunk_per_sentence=200,
        max_char_chunk=400,
        improve_max_chunk_chars=12000,
    )
    params["bema_episode"] = body.episode
    params["bema_url"] = f"https://www.bemadiscipleship.com/{body.episode}"
    metadata.update(
        {
            "id": project_id,
            "bema_episode": body.episode,
            "bema_url": f"https://www.bemadiscipleship.com/{body.episode}",
            "transcript_filename": transcript_filename or None,
            "transcript_uploaded": bool(transcript_filename),
        }
    )
    write_json(config_dir / "params.json", params)
    write_json(source_path.with_suffix(".params.json"), params)
    write_json(root / "metadata.json", metadata)
    write_json(root / "status.json", {
        "project_id": project_id,
        "state": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Waiting for worker",
        "updated_at": now_iso(),
    })
    write_json(root / "manifest.json", {"project_id": project_id, "artifacts": [], "stages": []})
    return project_summary(root)


@app.get("/api/projects/{project_id}/files/{filename}")
def get_project_file(project_id: str, filename: str) -> PlainTextResponse:
    root = project_path(project_id)
    safe_filename = safe_name(filename)
    if not (is_improved_filename(safe_filename) or safe_filename == "source.custom-instructions.txt"):
        raise HTTPException(status_code=404, detail="File not editable")
    path = None
    for directory in ("work", "output", "input"):
        candidate = root / directory / safe_filename
        if candidate.exists():
            path = candidate
            break
    if path is None and safe_filename == "source.custom-instructions.txt":
        candidate = root / "work" / safe_filename
        if candidate.exists():
            path = candidate
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@app.put("/api/projects/{project_id}/files/{filename}")
def save_project_file(project_id: str, filename: str, body: FileContentUpdateRequest) -> dict[str, Any]:
    root = project_path(project_id)
    safe_filename = safe_name(filename)
    if not is_improved_filename(safe_filename):
        raise HTTPException(status_code=404, detail="File not editable")
    path = None
    for directory in ("work", "output", "input"):
        candidate = root / directory / safe_filename
        if candidate.exists():
            path = candidate
            break
    if path is None:
        path = root / "work" / safe_filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    work_canonical = root / "work" / "source.improved.json"
    work_canonical.parent.mkdir(parents=True, exist_ok=True)
    work_canonical.write_text(body.content, encoding="utf-8")
    return {"ok": True, "filename": safe_filename}


@app.post("/api/projects/{project_id}/voiceover")
def start_voiceover(project_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    improved_path = improved_artifact_for_project(root) or (root / "work" / "source.improved.json")
    if not improved_path.exists():
        raise HTTPException(status_code=400, detail="Improved transcript is missing")
    canonical_improved = root / "work" / "source.improved.json"
    canonical_improved.parent.mkdir(parents=True, exist_ok=True)
    if improved_path != canonical_improved:
        canonical_improved.write_text(improved_path.read_text(encoding="utf-8"), encoding="utf-8")

    params = read_json(root / "config" / "params.json", {})
    params["stage_preset"] = "voiceover"
    params["stages_to_run"] = "voiceover"
    params["resume_from_improved"] = True
    write_json(root / "config" / "params.json", params)
    source_path = source_path_for_project(root)
    if source_path is not None:
        write_json(source_path.with_suffix(".params.json"), params)
    status = {
        "project_id": project_id,
        "state": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued for voiceover from improved transcript",
        "updated_at": now_iso(),
    }
    write_json(root / "status.json", status)
    manifest = read_json(root / "manifest.json", {"project_id": project_id, "artifacts": [], "stages": []})
    manifest.setdefault("stages", [])
    write_json(root / "manifest.json", manifest)
    return project_summary(root)


@app.get("/api/projects/{project_id}/improved-file")
def get_improved_file(project_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    improved_path = improved_artifact_for_project(root)
    if improved_path is None:
        raise HTTPException(status_code=404, detail="Improved transcript not found")
    return {"filename": improved_path.name}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return project_summary(project_path(project_id))


@app.get("/api/projects/{project_id}/logs", response_class=PlainTextResponse)
def get_project_logs(project_id: str) -> str:
    root = project_path(project_id)
    logs = []
    source_path = source_path_for_project(root)
    log_dirs = [root / "logs"]
    if source_path is not None:
        log_dirs.append(source_path.parent / ".log")
    for log_dir in log_dirs:
        for log_path in sorted(log_dir.glob("*.log")):
            logs.append(f"===== {log_path.relative_to(root)} =====\n")
            logs.append(log_path.read_text(encoding="utf-8", errors="replace")[-12000:])
            logs.append("\n")
    for log_zip in legacy_artifact_paths(root):
        if log_zip.name.endswith(".logs.zip"):
            logs.append(f"===== {log_zip.relative_to(root)} =====\n")
            logs.append("Legacy log bundle is available in artifacts.\n")
            logs.append("\n")
    return "".join(logs) or "No logs yet.\n"


@app.get("/api/projects/{project_id}/artifacts")
def get_artifacts(project_id: str) -> list[dict[str, Any]]:
    root = project_path(project_id)
    artifacts = []
    seen_names: set[str] = set()

    def add_artifact(path: Path, relative_path: str) -> None:
        if path.name in seen_names or not path.is_file():
            return
        seen_names.add(path.name)
        artifacts.append(
            {
                "name": path.name,
                "path": relative_path,
                "bytes": path.stat().st_size,
                "download_url": f"/api/projects/{project_id}/download/{path.name}",
            }
        )

    for path in legacy_artifact_paths(root):
        add_artifact(path, str(path.relative_to(root)))
    for path in sorted((root / "output").glob("*")):
        if path.is_file():
            add_artifact(path, f"output/{path.name}")
    for path in sorted((root / "work").glob("source.*.json")):
        if path.is_file() and path.name in DOWNLOADABLE_WORK_FILES:
            add_artifact(path, f"work/{path.name}")
    for path in sorted((root / "work").glob("*.improved.json")):
        add_artifact(path, f"work/{path.name}")
    for path in sorted((root / "input").glob("*.improved.json")):
        add_artifact(path, f"input/{path.name}")
    return artifacts


@app.get("/api/projects/{project_id}/download/{filename}")
def download_artifact(project_id: str, filename: str) -> FileResponse:
    root = project_path(project_id)
    safe_filename = safe_name(filename)
    artifact = root / "output" / safe_filename
    if not artifact.exists() and safe_filename in DOWNLOADABLE_WORK_FILES:
        artifact = root / "work" / safe_filename
    if not artifact.exists():
        source_path = source_path_for_project(root)
        if source_path is not None:
            artifact = source_path.parent / safe_filename
    if not artifact.exists() or not artifact.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path=artifact, filename=artifact.name)


@app.get("/api/projects/{project_id}/support-bundle")
def support_bundle(project_id: str) -> FileResponse:
    root = project_path(project_id)
    bundle_path = root / "output" / f"{project_id}.support.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        source_path = source_path_for_project(root)
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
        if source_path is not None:
            params_path = source_path.with_suffix(".params.json")
            if params_path.exists():
                bundle.write(params_path, str(params_path.relative_to(root)))
            for path in legacy_artifact_paths(root):
                bundle.write(path, str(path.relative_to(root)))
            for log_path in (source_path.parent / ".log").glob("*.log"):
                bundle.write(log_path, str(log_path.relative_to(root)))
    return FileResponse(path=bundle_path, filename=bundle_path.name)
