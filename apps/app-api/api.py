from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

import requests

import segments as seg


PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "/data/projects"))
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADABLE_WORK_FILES = {
    "source.raw.json",
    "source.stt-provider-response.json",
    "source.diarization.json",
    "source.combined.json",
    "source.translated.json",
    "source.improved.json",
    "source.custom-instructions.json",
    "source.custom-instructions.txt",
}
# `tts` and `voiceover-build` are the two halves of `voiceover`, for reruns that
# should not redo the other half.
STAGE_OPTIONS = {"transcribe", "translate", "customize", "improve", "voiceover", "tts", "voiceover-build"}
BUSY_STATES = {"queued", "running"}
SEGMENT_UPLOAD_MAX_BYTES = int(os.getenv("SEGMENT_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
SEGMENT_MEDIA_TYPES = {
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
}
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
DEFAULT_SPEEDUP_VALUE = 1.2
DEFAULT_VOICEOVER_TEMPO = 1.2
DEFAULT_VOICEOVER_SHIFT = 1.5
DEFAULT_MAX_PREVIEW_SIZE_MB = 2.0
DEFAULT_STT_CHUNK_LENGTH_SEC = 300
DEFAULT_STT_SILENCE_SEC = 2.0
DEFAULT_STT_PROVIDER = "openai"
# Kept in sync with worker/stt/registry.py; the API only needs the names.
SUPPORTED_STT_PROVIDERS = {"openai", "local-whisper"}
# Providers that reach OpenAI, per stage, so the key is only demanded when used.
OPENAI_STT_PROVIDERS = {"openai"}
DEFAULT_NUMBER_OF_SPEAKERS = 2
DEFAULT_MAX_CHAR_CHUNK_PER_SENTENCE = 200
DEFAULT_MAX_CHAR_CHUNK = 400
DEFAULT_IMPROVE_MAX_CHUNK_CHARS = 12000
VIBEVOICE_DEFAULT_VOICE = "SEBBE"
VIBEVOICE_VOICES_TIMEOUT_SECONDS = 5.0

app = FastAPI(title="Podocracy Worker Portal API")
app.add_middleware(
    CORSMiddleware,
    # Deliberately permissive for the local portal + same-host Docker workflow.
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@dataclass(frozen=True)
class ProjectDirs:
    project_id: str
    root: Path
    input_dir: Path
    output_dir: Path
    logs_dir: Path
    config_dir: Path
    work_dir: Path


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


def is_custom_instructions_filename(filename: str) -> bool:
    # Worker names these after the source stem, so match by suffix rather than an exact name.
    return filename.endswith(".custom-instructions.txt") or filename.endswith(
        ".custom-instructions.autogenerated.txt"
    )


def find_project_file(root: Path, suffix: str) -> Path | None:
    for directory in (root / "work", root / "output", root / "input"):
        for path in sorted(directory.glob(f"*{suffix}")):
            if path.is_file():
                return path
    return None


def improved_artifact_for_project(root: Path) -> Path | None:
    # The worker reads and writes the file named after the source stem. Resolving
    # to anything else lets the editor and the pipeline drift onto two copies.
    source_path = source_path_for_project(root)
    if source_path is not None:
        canonical = source_path.parent / f"{source_path.stem}.improved.json"
        if canonical.is_file():
            return canonical
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


def parse_int(value: str, field_name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    text = (value or "").strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a whole number") from exc
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


def optional_form_text(value: Any) -> str:
    # These endpoints are also called directly (tests, scripts), where an unpassed
    # optional argument is still the Form(...) default object rather than a string.
    return value.strip() if isinstance(value, str) else ""


def parse_tts_api(value: str) -> str:
    tts_api = (value or "openai").strip().lower()
    if tts_api not in {"openai", "elevenlabs", "vibevoice"}:
        raise HTTPException(status_code=400, detail="tts_api must be openai, elevenlabs, or vibevoice")
    return tts_api


def parse_stt_provider(value: str) -> str:
    provider = (optional_form_text(value) or DEFAULT_STT_PROVIDER).lower()
    # Accept the legacy spellings a scripted client may still send.
    provider = {"whisper-api": "openai", "openai-whisper": "openai", "whisper": "local-whisper"}.get(provider, provider)
    if provider not in SUPPORTED_STT_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_STT_PROVIDERS))
        raise HTTPException(status_code=400, detail=f"stt_provider must be one of: {supported}")
    return provider


def parse_translation_provider(value: str) -> str:
    provider = (value or "openai").strip().lower()
    if provider not in {"openai", "deepl"}:
        raise HTTPException(status_code=400, detail="translation_provider must be openai or deepl")
    return provider


def parse_target_language(value: str) -> str:
    language = (value or "").strip().upper()
    if not language:
        raise HTTPException(status_code=400, detail="language is required")
    if language not in SUPPORTED_TARGET_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_TARGET_LANGUAGES))
        raise HTTPException(status_code=400, detail=f"language must be one of: {supported}")
    return language


def bema_headers() -> dict[str, str]:
    # BEMA blocks some generic bot UAs; this endpoint scrapes third-party HTML that may change without notice.
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
        transcript_url = urljoin(bema_url, unescape(transcript_link_match.group(1)))
        try:
            transcript_response = requests.get(transcript_url, headers=bema_headers(), timeout=30)
            transcript_response.raise_for_status()
            transcript_text = strip_html_to_text(transcript_response.text)
        except requests.RequestException:
            transcript_text = ""

    return mp3_filename, mp3_response.content, transcript_text


def create_project_root() -> ProjectDirs:
    project_id = f"project-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    root = PROJECTS_DIR / project_id
    input_dir = root / "input"
    output_dir = root / "output"
    logs_dir = root / "logs"
    config_dir = root / "config"
    work_dir = root / "work"
    for directory in (input_dir, output_dir, logs_dir, config_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return ProjectDirs(project_id, root, input_dir, output_dir, logs_dir, config_dir, work_dir)


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
    stt_provider: str = DEFAULT_STT_PROVIDER,
    elevenlabs_voice_id: str = "",
    vibevoice_model: str = "",
    vibevoice_cfg_scale: str = "",
    vibevoice_speed: str = "",
    voiceover_tempo: float | None = None,
    voiceover_shift: float | None = None,
    normalize_final_audio: bool = False,
    max_preview_size_mb: float = DEFAULT_MAX_PREVIEW_SIZE_MB,
    use_subtitles_as_is: bool = False,
    autogenerate_custom_instructions: bool = False,
    detailed_transcription: bool = True,
    speaker_recognition: bool = False,
    number_of_speakers: int = DEFAULT_NUMBER_OF_SPEAKERS,
    stt_chunk_length_sec: int = DEFAULT_STT_CHUNK_LENGTH_SEC,
    stt_silence_split: bool = False,
    stt_silence_sec: float = DEFAULT_STT_SILENCE_SEC,
    max_char_chunk_per_sentence: int = DEFAULT_MAX_CHAR_CHUNK_PER_SENTENCE,
    max_char_chunk: int = DEFAULT_MAX_CHAR_CHUNK,
    improve_max_chunk_chars: int = DEFAULT_IMPROVE_MAX_CHUNK_CHARS,
    subtitle_relative: str = "",
    custom_recordings_relative: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed_tts_api = parse_tts_api(tts_api)
    parsed_language = parse_target_language(language)
    parsed_translation_provider = parse_translation_provider(translation_provider)
    parsed_stt_provider = parse_stt_provider(stt_provider)
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
        "stt_provider": parsed_stt_provider,
        # Legacy mirror of stt_provider, so a worker built before the provider
        # boundary still selects the same engine from this params file.
        "whisper_api": parsed_stt_provider in OPENAI_STT_PROVIDERS,
        "tts_api": parsed_tts_api,
        "translation_provider": parsed_translation_provider,
        "translation_text_key": "dltrans",
        "improved_text_key": "imp",
        "speedup_value": DEFAULT_SPEEDUP_VALUE,
        "normalize_final_audio": normalize_final_audio,
        "use_subtitles_as_is": use_subtitles_as_is,
        "autogenerate_custom_instructions": autogenerate_custom_instructions,
        "detailed_transcription": detailed_transcription,
        "speaker_recognition": speaker_recognition,
        "number_of_speakers": number_of_speakers,
        "stt_chunk_length_sec": stt_chunk_length_sec,
        "stt_silence_split": stt_silence_split,
        "stt_silence_sec": stt_silence_sec,
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
    for key, raw in (
        ("vibevoice_model", vibevoice_model),
        ("vibevoice_cfg_scale", vibevoice_cfg_scale),
        ("vibevoice_speed", vibevoice_speed),
    ):
        cleaned = optional_form_text(raw)
        if cleaned:
            params[key] = cleaned
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
        "stt_provider": parsed_stt_provider,
        "voiceover_tempo": voiceover_tempo if voiceover_tempo is not None else DEFAULT_VOICEOVER_TEMPO,
        "voiceover_shift": voiceover_shift if voiceover_shift is not None else DEFAULT_VOICEOVER_SHIFT,
        "custom_subtitles": bool(subtitle_relative),
        "custom_recording": bool(custom_recordings_relative),
        "normalize_final_audio": normalize_final_audio,
        "autogenerate_custom_instructions": autogenerate_custom_instructions,
        "detailed_transcription": detailed_transcription,
        "speaker_recognition": speaker_recognition,
        "number_of_speakers": number_of_speakers,
    }
    return params, metadata


def openai_stages(
    stages_to_run: str,
    *,
    stt_provider: str,
    translation_provider: str,
    tts_api: str,
) -> list[str]:
    """Selected stages that call OpenAI, so the key is only required when used.

    Improve and customize have no provider switch yet, so they always count.
    """
    stages = [stage for stage in stages_to_run.split("+") if stage]
    needed = []
    for stage in stages:
        if stage == "transcribe" and stt_provider in OPENAI_STT_PROVIDERS:
            needed.append(stage)
        elif stage == "translate" and translation_provider == "openai":
            needed.append(stage)
        elif stage in {"customize", "improve"}:
            needed.append(stage)
        elif stage == "voiceover" and tts_api == "openai":
            needed.append(stage)
    return needed


def build_configured_project_payload(
    *,
    filename: str,
    language: str,
    voice: str,
    stage_preset: str,
    stages_to_run: str,
    custom_instructions: str,
    tts_api: str,
    translation_provider: str,
    stt_provider: str,
    elevenlabs_voice_id: str,
    vibevoice_model: str = "",
    vibevoice_cfg_scale: str = "",
    vibevoice_speed: str = "",
    voiceover_tempo: str,
    voiceover_shift: str,
    normalize_final_audio: str,
    max_preview_size_mb: str,
    use_subtitles_as_is: str,
    autogenerate_custom_instructions: str,
    detailed_transcription: str,
    speaker_recognition: str,
    number_of_speakers: str,
    stt_chunk_length_sec: str,
    stt_silence_split: str,
    stt_silence_sec: str,
    max_char_chunk_per_sentence: str,
    max_char_chunk: str,
    improve_max_chunk_chars: str,
    subtitle_relative: str = "",
    custom_recordings_relative: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    providers_present = provider_status()

    parsed_language = parse_target_language(language)
    parsed_translation_provider = parse_translation_provider(translation_provider)
    parsed_stt_provider = parse_stt_provider(stt_provider)
    if parsed_translation_provider == "deepl" and not providers_present["deepl"]:
        raise HTTPException(status_code=400, detail="DEEPL_AUTH_KEY is required for DeepL translation")
    resolved_stages_to_run = normalize_stage_list(stages_to_run, stage_preset)
    parsed_tts_api = parse_tts_api(tts_api)

    if not providers_present["openai"]:
        needed_by = openai_stages(
            resolved_stages_to_run,
            stt_provider=parsed_stt_provider,
            translation_provider=parsed_translation_provider,
            tts_api=parsed_tts_api,
        )
        if needed_by:
            raise HTTPException(
                status_code=400,
                detail=f"OPENAI_API_KEY is required for these stages: {', '.join(needed_by)}",
            )
    if "voiceover" in resolved_stages_to_run and parsed_tts_api == "elevenlabs" and not providers_present["elevenlabs"]:
        raise HTTPException(status_code=400, detail="ELEVENLABS_API_KEY is required for ElevenLabs TTS")
    if "voiceover" in resolved_stages_to_run and parsed_tts_api == "vibevoice" and not providers_present["vibevoice"]:
        raise HTTPException(status_code=400, detail="VIBEVOICE_BASE_URL is required for VibeVoice TTS")

    parsed_voiceover_tempo = parse_optional_float(voiceover_tempo, "voiceover_tempo", 0.5, 2.0)
    parsed_voiceover_shift = parse_optional_float(voiceover_shift, "voiceover_shift", -300.0, 300.0)
    parsed_max_preview_size_mb = parse_optional_float(max_preview_size_mb, "max_preview_size_mb", 0.1, 500.0)
    parsed_stt_chunk_length_sec = parse_optional_float(stt_chunk_length_sec, "stt_chunk_length_sec", 10.0, 3600.0)
    parsed_stt_silence_sec = parse_optional_float(stt_silence_sec, "stt_silence_sec", 0.1, 30.0)
    parsed_max_char_chunk_per_sentence = parse_optional_float(max_char_chunk_per_sentence, "max_char_chunk_per_sentence", 20.0, 5000.0)
    parsed_max_char_chunk = parse_optional_float(max_char_chunk, "max_char_chunk", 50.0, 20000.0)
    parsed_improve_max_chunk_chars = parse_optional_float(improve_max_chunk_chars, "improve_max_chunk_chars", 500.0, 200000.0)
    parsed_number_of_speakers = parse_int(
        number_of_speakers,
        "number_of_speakers",
        DEFAULT_NUMBER_OF_SPEAKERS,
        1,
        20,
    )

    return build_project_payload(
        filename=filename,
        language=parsed_language,
        voice=voice,
        stage_preset=stage_preset,
        stages_to_run=resolved_stages_to_run,
        custom_instructions=custom_instructions,
        tts_api=parsed_tts_api,
        translation_provider=parsed_translation_provider,
        stt_provider=parsed_stt_provider,
        elevenlabs_voice_id=elevenlabs_voice_id,
        vibevoice_model=vibevoice_model,
        vibevoice_cfg_scale=vibevoice_cfg_scale,
        vibevoice_speed=vibevoice_speed,
        voiceover_tempo=parsed_voiceover_tempo,
        voiceover_shift=parsed_voiceover_shift,
        normalize_final_audio=parse_optional_bool(normalize_final_audio),
        max_preview_size_mb=parsed_max_preview_size_mb if parsed_max_preview_size_mb is not None else DEFAULT_MAX_PREVIEW_SIZE_MB,
        use_subtitles_as_is=parse_optional_bool(use_subtitles_as_is),
        autogenerate_custom_instructions=parse_optional_bool(autogenerate_custom_instructions),
        detailed_transcription=parse_bool(detailed_transcription, default=True),
        speaker_recognition=parse_optional_bool(speaker_recognition),
        number_of_speakers=parsed_number_of_speakers,
        stt_chunk_length_sec=int(parsed_stt_chunk_length_sec or DEFAULT_STT_CHUNK_LENGTH_SEC),
        stt_silence_split=parse_optional_bool(stt_silence_split),
        stt_silence_sec=parsed_stt_silence_sec if parsed_stt_silence_sec is not None else DEFAULT_STT_SILENCE_SEC,
        max_char_chunk_per_sentence=int(parsed_max_char_chunk_per_sentence or DEFAULT_MAX_CHAR_CHUNK_PER_SENTENCE),
        max_char_chunk=int(parsed_max_char_chunk or DEFAULT_MAX_CHAR_CHUNK),
        improve_max_chunk_chars=int(parsed_improve_max_chunk_chars or DEFAULT_IMPROVE_MAX_CHUNK_CHARS),
        subtitle_relative=subtitle_relative,
        custom_recordings_relative=custom_recordings_relative,
    )


def provider_status() -> dict[str, bool]:
    # Pure env-var checks. /api/health and /api/providers are polled by the UI and
    # the launcher readiness loop, so a blocking network probe here would make both
    # hang whenever the local TTS server is down.
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "deepl": bool(os.getenv("DEEPL_AUTH_KEY")),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
        "vibevoice": bool((os.getenv("VIBEVOICE_BASE_URL") or "").strip()),
        # Runs inside the worker, so it needs no credential of its own.
        "local-whisper": True,
    }


def vibevoice_base_url() -> str:
    """Endpoint is operator-controlled: env only, never a request field (SSRF)."""
    raw = (os.getenv("VIBEVOICE_BASE_URL") or "").strip()
    if not raw:
        raise HTTPException(status_code=503, detail="VIBEVOICE_BASE_URL is not set")
    if urlparse(raw).scheme.lower() not in {"http", "https"}:
        raise HTTPException(status_code=503, detail="VIBEVOICE_BASE_URL must use http or https")
    return raw.rstrip("/")


def normalize_vibevoice_voices(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        for key in ("voices", "data"):
            if key in payload:
                return normalize_vibevoice_voices(payload[key])
        return []
    if not isinstance(payload, list):
        return []
    voices: list[str] = []
    for entry in payload:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            raw = entry.get("id") or entry.get("name") or entry.get("voice") or ""
            name = str(raw).strip()
        else:
            name = ""
        if name and name not in voices:
            voices.append(name)
    return voices


@app.get("/api/tts/vibevoice/voices")
def vibevoice_voices() -> dict[str, Any]:
    base_url = vibevoice_base_url()
    try:
        response = requests.get(f"{base_url}/voices", timeout=VIBEVOICE_VOICES_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        # 503, never 500, and never reflecting the upstream body back to the browser:
        # a stopped local server must not be able to knock over the portal API.
        raise HTTPException(
            status_code=503,
            detail=f"VibeVoice server not reachable at {base_url}. Start it and retry.",
        ) from None

    voices = normalize_vibevoice_voices(payload)
    default = ""
    if isinstance(payload, dict):
        default = str(payload.get("default") or payload.get("default_voice") or "").strip()
    if not default:
        default = (os.getenv("VIBEVOICE_TTS_VOICE") or "").strip()
    if not default or (voices and default not in voices):
        default = voices[0] if voices else VIBEVOICE_DEFAULT_VOICE
    return {"default": default, "voices": voices}


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
    language: str = Form(...),
    voice: str = Form("alloy"),
    stage_preset: str = Form("voiceover"),
    stages_to_run: str = Form(""),
    custom_instructions: str = Form(""),
    tts_api: str = Form("openai"),
    translation_provider: str = Form("openai"),
    stt_provider: str = Form(DEFAULT_STT_PROVIDER),
    elevenlabs_voice_id: str = Form(""),
    vibevoice_model: str = Form(""),
    vibevoice_cfg_scale: str = Form(""),
    vibevoice_speed: str = Form(""),
    voiceover_tempo: str = Form(str(DEFAULT_VOICEOVER_TEMPO)),
    voiceover_shift: str = Form(str(DEFAULT_VOICEOVER_SHIFT)),
    normalize_final_audio: str = Form(""),
    max_preview_size_mb: str = Form(str(DEFAULT_MAX_PREVIEW_SIZE_MB)),
    use_subtitles_as_is: str = Form(""),
    autogenerate_custom_instructions: str = Form(""),
    detailed_transcription: str = Form("true"),
    speaker_recognition: str = Form(""),
    number_of_speakers: str = Form(str(DEFAULT_NUMBER_OF_SPEAKERS)),
    stt_chunk_length_sec: str = Form(""),
    stt_silence_split: str = Form(""),
    stt_silence_sec: str = Form(""),
    max_char_chunk_per_sentence: str = Form(str(DEFAULT_MAX_CHAR_CHUNK_PER_SENTENCE)),
    max_char_chunk: str = Form(str(DEFAULT_MAX_CHAR_CHUNK)),
    improve_max_chunk_chars: str = Form(str(DEFAULT_IMPROVE_MAX_CHUNK_CHARS)),
    # Pre-rename field names, still accepted from scripted clients.
    whisper_chunk_length_sec: str = Form(""),
    whisper_silence_split: str = Form(""),
    whisper_silence_sec: str = Form(""),
) -> dict[str, Any]:
    if not source_url.strip() and (source is None or not source.filename):
        raise HTTPException(status_code=400, detail="Upload a source file or provide a source URL")

    if source_url.strip():
        filename = "source.url"
    else:
        assert source is not None
        filename = safe_name(source.filename or "source.mp3")
    subtitle_name = ""
    subtitle_relative = ""
    if subtitle_file and subtitle_file.filename:
        subtitle_name = f"{Path(filename).stem}.subtitles{Path(safe_name(subtitle_file.filename)).suffix.lower() or '.srt'}"
        subtitle_relative = f"input/{subtitle_name}"
    custom_recordings_relative = "input/custom-recordings.zip" if custom_recordings and custom_recordings.filename else ""

    params, metadata = build_configured_project_payload(
        filename=filename,
        language=language,
        voice=voice,
        stage_preset=stage_preset,
        stages_to_run=stages_to_run,
        custom_instructions=custom_instructions,
        tts_api=tts_api,
        translation_provider=translation_provider,
        stt_provider=stt_provider,
        elevenlabs_voice_id=elevenlabs_voice_id,
        vibevoice_model=vibevoice_model,
        vibevoice_cfg_scale=vibevoice_cfg_scale,
        vibevoice_speed=vibevoice_speed,
        voiceover_tempo=voiceover_tempo,
        voiceover_shift=voiceover_shift,
        normalize_final_audio=normalize_final_audio,
        max_preview_size_mb=max_preview_size_mb,
        use_subtitles_as_is=use_subtitles_as_is,
        autogenerate_custom_instructions=autogenerate_custom_instructions,
        detailed_transcription=detailed_transcription,
        speaker_recognition=speaker_recognition,
        number_of_speakers=number_of_speakers,
        stt_chunk_length_sec=optional_form_text(stt_chunk_length_sec) or optional_form_text(whisper_chunk_length_sec),
        stt_silence_split=optional_form_text(stt_silence_split) or optional_form_text(whisper_silence_split),
        stt_silence_sec=optional_form_text(stt_silence_sec) or optional_form_text(whisper_silence_sec),
        max_char_chunk_per_sentence=max_char_chunk_per_sentence,
        max_char_chunk=max_char_chunk,
        improve_max_chunk_chars=improve_max_chunk_chars,
        subtitle_relative=subtitle_relative,
        custom_recordings_relative=custom_recordings_relative,
    )

    project_dirs = create_project_root()
    root = project_dirs.root
    input_dir = project_dirs.input_dir
    config_dir = project_dirs.config_dir

    source_path = input_dir / filename
    if source_url.strip():
        write_json(source_path, {"url": source_url.strip()})
    else:
        assert source is not None
        with source_path.open("wb") as handle:
            shutil.copyfileobj(source.file, handle)

    if subtitle_name:
        subtitle_path = input_dir / subtitle_name
        assert subtitle_file is not None
        with subtitle_path.open("wb") as handle:
            shutil.copyfileobj(subtitle_file.file, handle)

    if custom_recordings_relative:
        recordings_path = input_dir / "custom-recordings.zip"
        assert custom_recordings is not None
        with recordings_path.open("wb") as handle:
            shutil.copyfileobj(custom_recordings.file, handle)
    write_json(config_dir / "params.json", params)
    write_json(source_path.with_suffix(".params.json"), params)

    metadata["id"] = project_dirs.project_id
    status = {
        "project_id": project_dirs.project_id,
        "state": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Waiting for worker",
        "updated_at": now_iso(),
    }
    write_json(root / "metadata.json", metadata)
    write_json(root / "status.json", status)
    write_json(root / "manifest.json", {"project_id": project_dirs.project_id, "artifacts": [], "stages": []})

    return project_summary(root)


class ImportBemaEpisodeRequest(BaseModel):
    episode: int
    include_transcript: bool = True


class FileContentUpdateRequest(BaseModel):
    content: str


class CustomInstructionsUpdateRequest(BaseModel):
    custom_instructions: str


@app.post("/api/projects/bema")
def import_bema_episode(body: ImportBemaEpisodeRequest) -> dict[str, Any]:
    if body.episode <= 0 or body.episode > 9999:
        raise HTTPException(status_code=400, detail="episode must be between 1 and 9999")

    mp3_filename, mp3_content, transcript_text = download_bema_episode(body.episode)
    project_dirs = create_project_root()
    source_path = project_dirs.input_dir / mp3_filename
    source_path.write_bytes(mp3_content)

    transcript_filename = ""
    transcript_warning = None
    if body.include_transcript and transcript_text:
        transcript_filename = f"{source_path.stem}.proofread.txt"
        transcript_path = project_dirs.input_dir / transcript_filename
        transcript_path.write_text(transcript_text, encoding="utf-8")
    elif body.include_transcript:
        transcript_warning = "BEMA episode transcript could not be downloaded"

    if transcript_filename:
        status_message = "Audio and transcript imported. Configure the project to start processing."
    elif transcript_warning:
        status_message = "Audio imported without transcript. Configure the project to start processing."
    else:
        status_message = "Audio imported. Configure the project to start processing."

    metadata = {
        "id": project_dirs.project_id,
        "created_at": now_iso(),
        "source_filename": mp3_filename,
        "source_path": f"input/{mp3_filename}",
        "bema_episode": body.episode,
        "bema_url": f"https://www.bemadiscipleship.com/{body.episode}",
        "transcript_filename": transcript_filename or None,
        "transcript_uploaded": bool(transcript_filename),
        "transcript_warning": transcript_warning,
    }
    write_json(project_dirs.root / "metadata.json", metadata)
    write_json(project_dirs.root / "status.json", {
        "project_id": project_dirs.project_id,
        "state": "draft",
        "stage": "configuration",
        "progress": 0,
        "message": status_message,
        "updated_at": now_iso(),
    })
    write_json(
        project_dirs.root / "manifest.json",
        {"project_id": project_dirs.project_id, "artifacts": [], "stages": []},
    )
    return project_summary(project_dirs.root)


@app.post("/api/projects/{project_id}/start")
def start_draft_project(
    project_id: str,
    subtitle_file: UploadFile | None = File(None),
    custom_recordings: UploadFile | None = File(None),
    language: str = Form(...),
    voice: str = Form("alloy"),
    stage_preset: str = Form("voiceover"),
    stages_to_run: str = Form(""),
    custom_instructions: str = Form(""),
    tts_api: str = Form("openai"),
    translation_provider: str = Form("openai"),
    stt_provider: str = Form(DEFAULT_STT_PROVIDER),
    elevenlabs_voice_id: str = Form(""),
    vibevoice_model: str = Form(""),
    vibevoice_cfg_scale: str = Form(""),
    vibevoice_speed: str = Form(""),
    voiceover_tempo: str = Form(str(DEFAULT_VOICEOVER_TEMPO)),
    voiceover_shift: str = Form(str(DEFAULT_VOICEOVER_SHIFT)),
    normalize_final_audio: str = Form(""),
    max_preview_size_mb: str = Form(str(DEFAULT_MAX_PREVIEW_SIZE_MB)),
    use_subtitles_as_is: str = Form(""),
    autogenerate_custom_instructions: str = Form(""),
    detailed_transcription: str = Form("true"),
    speaker_recognition: str = Form(""),
    number_of_speakers: str = Form(str(DEFAULT_NUMBER_OF_SPEAKERS)),
    stt_chunk_length_sec: str = Form(""),
    stt_silence_split: str = Form(""),
    stt_silence_sec: str = Form(""),
    max_char_chunk_per_sentence: str = Form(str(DEFAULT_MAX_CHAR_CHUNK_PER_SENTENCE)),
    max_char_chunk: str = Form(str(DEFAULT_MAX_CHAR_CHUNK)),
    improve_max_chunk_chars: str = Form(str(DEFAULT_IMPROVE_MAX_CHUNK_CHARS)),
    # Pre-rename field names, still accepted from scripted clients.
    whisper_chunk_length_sec: str = Form(""),
    whisper_silence_split: str = Form(""),
    whisper_silence_sec: str = Form(""),
) -> dict[str, Any]:
    root = project_path(project_id)
    status = read_json(root / "status.json", {})
    if status.get("state") != "draft":
        raise HTTPException(status_code=409, detail="Only draft projects can be started")

    source_path = source_path_for_project(root)
    if source_path is None:
        raise HTTPException(status_code=400, detail="Draft source file is missing")

    subtitle_name = ""
    subtitle_relative = ""
    if subtitle_file and subtitle_file.filename:
        subtitle_name = f"{source_path.stem}.subtitles{Path(safe_name(subtitle_file.filename)).suffix.lower() or '.srt'}"
        subtitle_relative = f"input/{subtitle_name}"

    custom_recordings_relative = "input/custom-recordings.zip" if custom_recordings and custom_recordings.filename else ""

    params, configured_metadata = build_configured_project_payload(
        filename=source_path.name,
        language=language,
        voice=voice,
        stage_preset=stage_preset,
        stages_to_run=stages_to_run,
        custom_instructions=custom_instructions,
        tts_api=tts_api,
        translation_provider=translation_provider,
        stt_provider=stt_provider,
        elevenlabs_voice_id=elevenlabs_voice_id,
        vibevoice_model=vibevoice_model,
        vibevoice_cfg_scale=vibevoice_cfg_scale,
        vibevoice_speed=vibevoice_speed,
        voiceover_tempo=voiceover_tempo,
        voiceover_shift=voiceover_shift,
        normalize_final_audio=normalize_final_audio,
        max_preview_size_mb=max_preview_size_mb,
        use_subtitles_as_is=use_subtitles_as_is,
        autogenerate_custom_instructions=autogenerate_custom_instructions,
        detailed_transcription=detailed_transcription,
        speaker_recognition=speaker_recognition,
        number_of_speakers=number_of_speakers,
        stt_chunk_length_sec=optional_form_text(stt_chunk_length_sec) or optional_form_text(whisper_chunk_length_sec),
        stt_silence_split=optional_form_text(stt_silence_split) or optional_form_text(whisper_silence_split),
        stt_silence_sec=optional_form_text(stt_silence_sec) or optional_form_text(whisper_silence_sec),
        max_char_chunk_per_sentence=max_char_chunk_per_sentence,
        max_char_chunk=max_char_chunk,
        improve_max_chunk_chars=improve_max_chunk_chars,
        subtitle_relative=subtitle_relative,
        custom_recordings_relative=custom_recordings_relative,
    )
    if subtitle_name:
        subtitle_path = root / "input" / subtitle_name
        assert subtitle_file is not None
        with subtitle_path.open("wb") as handle:
            shutil.copyfileobj(subtitle_file.file, handle)
    if custom_recordings_relative:
        recordings_path = root / "input" / "custom-recordings.zip"
        assert custom_recordings is not None
        with recordings_path.open("wb") as handle:
            shutil.copyfileobj(custom_recordings.file, handle)

    metadata = read_json(root / "metadata.json", {})
    configured_metadata.pop("created_at", None)
    metadata.update(configured_metadata)
    metadata["id"] = project_id
    metadata["configured_at"] = now_iso()
    if metadata.get("bema_episode") is not None:
        params["bema_episode"] = metadata["bema_episode"]
    if metadata.get("bema_url"):
        params["bema_url"] = metadata["bema_url"]

    write_json(root / "config" / "params.json", params)
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
    return project_summary(root)


@app.get("/api/projects/{project_id}/files/{filename}")
def get_project_file(project_id: str, filename: str) -> PlainTextResponse:
    root = project_path(project_id)
    safe_filename = safe_name(filename)
    if not (is_improved_filename(safe_filename) or is_custom_instructions_filename(safe_filename)):
        raise HTTPException(status_code=404, detail="File not editable")
    path = None
    for directory in ("work", "output", "input"):
        candidate = root / directory / safe_filename
        if candidate.exists():
            path = candidate
            break
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


CUSTOM_INSTRUCTIONS_SEPARATOR = "\n--------------\n"


def compose_custom_instructions(user_instructions: str, autogenerated_instructions: str) -> str:
    parts = ((user_instructions or "").strip(), (autogenerated_instructions or "").strip())
    return CUSTOM_INSTRUCTIONS_SEPARATOR.join(part for part in parts if part)


@app.get("/api/projects/{project_id}/instructions")
def get_project_instructions(project_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    params = read_json(root / "config" / "params.json", {})
    user_instructions = str(params.get("custom_instructions") or "")

    # The generated block lives in the input-dir params copy, which the worker refreshes each run.
    autogenerated = ""
    source_path = source_path_for_project(root)
    if source_path is not None:
        run_params = read_json(source_path.with_suffix(".params.json"), {})
        autogenerated = str(run_params.get("autogenerated_custom_instructions") or "")
    if not autogenerated:
        generated_file = find_project_file(root, ".custom-instructions.autogenerated.txt")
        if generated_file is not None:
            autogenerated = generated_file.read_text(encoding="utf-8")

    effective_file = find_project_file(root, ".custom-instructions.txt")
    effective_used = effective_file.read_text(encoding="utf-8") if effective_file is not None else ""

    return {
        "custom_instructions": user_instructions,
        "autogenerated_custom_instructions": autogenerated,
        "effective_preview": compose_custom_instructions(user_instructions, autogenerated),
        "effective_used": effective_used,
        "autogenerate_enabled": bool(params.get("autogenerate_custom_instructions")),
    }


@app.put("/api/projects/{project_id}/instructions")
def save_project_instructions(project_id: str, body: CustomInstructionsUpdateRequest) -> dict[str, Any]:
    root = project_path(project_id)
    config_path = root / "config" / "params.json"
    params = read_json(config_path, None)
    if params is None:
        raise HTTPException(status_code=404, detail="Project has no params to update")
    params["custom_instructions"] = body.custom_instructions
    write_json(config_path, params)
    return {"ok": True, "custom_instructions": params["custom_instructions"]}


def project_text_keys(params: dict[str, Any]) -> tuple[str, str]:
    return (
        str(params.get("improved_text_key") or "imp"),
        str(params.get("translation_text_key") or "dltrans"),
    )


def ensure_project_idle(root: Path) -> None:
    """One worker runs one project at a time, so a second job would either be
    dropped or race the first. Say so instead of pretending it was accepted."""
    status = read_json(root / "status.json", {})
    state = status.get("state")
    if state in BUSY_STATES:
        stage = status.get("stage") or "unknown"
        raise HTTPException(status_code=409, detail=f"Project is {state} ({stage}). Wait for it to finish.")


def write_improved_transcript(root: Path, path: Path, chunks: list[dict[str, Any]]) -> None:
    content = json.dumps(chunks, indent=2, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    canonical = root / "work" / "source.improved.json"
    if canonical != path:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text(content, encoding="utf-8")


def load_transcript_chunks(root: Path) -> tuple[Path, list[dict[str, Any]]]:
    """The transcript plus a guarantee that every chunk has a stable id."""
    path = improved_artifact_for_project(root)
    if path is None:
        raise HTTPException(status_code=404, detail="Improved transcript not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Improved transcript is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise HTTPException(status_code=400, detail="Improved transcript is not a chunk array")
    if seg.assign_chunk_ids(data):
        write_improved_transcript(root, path, data)
    return path, data


def find_chunk(chunks: list[dict[str, Any]], chunk_id: str) -> dict[str, Any]:
    chunk = next((item for item in chunks if item.get("chunk_id") == chunk_id), None)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} is not in the transcript")
    return chunk


def validate_chunk_id(chunk_id: str) -> str:
    if not seg.is_chunk_id(chunk_id):
        raise HTTPException(status_code=400, detail="Invalid chunk id")
    return chunk_id


def queue_voiceover_job(
    root: Path,
    *,
    stages: str,
    job_kind: str,
    message: str,
    chunk_ids: list[str] | None = None,
) -> dict[str, Any]:
    improved_path = improved_artifact_for_project(root)
    if improved_path is None or not improved_path.exists():
        raise HTTPException(status_code=400, detail="Improved transcript is missing")
    canonical_improved = root / "work" / "source.improved.json"
    canonical_improved.parent.mkdir(parents=True, exist_ok=True)
    if improved_path != canonical_improved:
        canonical_improved.write_text(improved_path.read_text(encoding="utf-8"), encoding="utf-8")

    params = read_json(root / "config" / "params.json", {})
    params["stage_preset"] = "voiceover"
    params["stages_to_run"] = stages
    params["resume_from_improved"] = True
    if chunk_ids:
        params["voiceover_chunks"] = chunk_ids
    else:
        # A previous scoped rerun must not silently scope this one.
        params.pop("voiceover_chunks", None)
    write_json(root / "config" / "params.json", params)
    source_path = source_path_for_project(root)
    if source_path is not None:
        write_json(source_path.with_suffix(".params.json"), params)
    write_json(
        root / "status.json",
        {
            "project_id": root.name,
            "state": "queued",
            "stage": "queued",
            "progress": 0,
            "job_kind": job_kind,
            "message": message,
            "updated_at": now_iso(),
        },
    )
    manifest = read_json(root / "manifest.json", {"project_id": root.name, "artifacts": [], "stages": []})
    manifest.setdefault("stages", [])
    write_json(root / "manifest.json", manifest)
    return project_summary(root)


@app.post("/api/projects/{project_id}/voiceover")
def start_voiceover(project_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    ensure_project_idle(root)
    return queue_voiceover_job(
        root,
        stages="voiceover",
        job_kind="voiceover",
        message="Queued for voiceover from improved transcript",
    )


@app.post("/api/projects/{project_id}/voiceover/synthesize")
def synthesize_missing_segments(project_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    ensure_project_idle(root)
    return queue_voiceover_job(
        root,
        stages="tts",
        job_kind="tts",
        message="Queued generation of missing and stale chunks",
    )


@app.post("/api/projects/{project_id}/voiceover/build")
def build_voiceover(project_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    ensure_project_idle(root)
    return queue_voiceover_job(
        root,
        stages="voiceover-build",
        job_kind="voiceover-build",
        message="Queued voiceover assembly from existing chunk audio",
    )


@app.get("/api/projects/{project_id}/segments")
def list_segments(project_id: str) -> dict[str, Any]:
    """Every chunk's audio state in one call. The editor renders one row per
    chunk, so a per-chunk request would mean hundreds of round trips."""
    root = project_path(project_id)
    path, chunks = load_transcript_chunks(root)
    params = read_json(root / "config" / "params.json", {})
    improved_key, translation_key = project_text_keys(params)
    status = read_json(root / "status.json", {})
    return {
        "project_id": project_id,
        "filename": path.name,
        "busy": status.get("state") in BUSY_STATES,
        "job_kind": status.get("job_kind"),
        "segments": seg.describe_chunks(
            root, chunks, params, improved_key=improved_key, translation_key=translation_key
        ),
    }


@app.get("/api/projects/{project_id}/segments/{chunk_id}/audio")
def get_segment_audio(project_id: str, chunk_id: str) -> FileResponse:
    root = project_path(project_id)
    validate_chunk_id(chunk_id)
    entry = seg.segment_entry(seg.load_index(root), chunk_id)
    audio = seg.audio_path(root, entry)
    if audio is None:
        raise HTTPException(status_code=404, detail="No audio for this chunk")
    extension = audio.suffix.lstrip(".").lower()
    return FileResponse(
        path=audio,
        media_type=SEGMENT_MEDIA_TYPES.get(extension, "application/octet-stream"),
        filename=audio.name,
    )


@app.put("/api/projects/{project_id}/segments/{chunk_id}/audio")
def upload_segment_audio(project_id: str, chunk_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Park a browser recording for the worker to convert.

    This image has no ffmpeg, so the upload is stored untouched and marked
    pending; the worker turns it into the canonical ogg on its next run. The raw
    file stays playable in the browser in the meantime.
    """
    root = project_path(project_id)
    validate_chunk_id(chunk_id)
    ensure_project_idle(root)
    _, chunks = load_transcript_chunks(root)
    chunk = find_chunk(chunks, chunk_id)

    extension = (Path(file.filename or "").suffix.lstrip(".") or "webm").lower()
    if extension not in seg.RAW_UPLOAD_EXTENSIONS:
        allowed = ", ".join(seg.RAW_UPLOAD_EXTENSIONS)
        raise HTTPException(status_code=400, detail=f"Unsupported audio type '.{extension}'. Allowed: {allowed}")
    payload = file.file.read(SEGMENT_UPLOAD_MAX_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="Recording is empty")
    if len(payload) > SEGMENT_UPLOAD_MAX_BYTES:
        limit_mb = SEGMENT_UPLOAD_MAX_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Recording exceeds the {limit_mb} MB limit")

    raw_dir = seg.raw_dir(root)
    raw_dir.mkdir(parents=True, exist_ok=True)
    # One take per chunk, so a re-record cannot leave the previous one behind in
    # a different container format.
    for stale in raw_dir.glob(f"{chunk_id}.*"):
        stale.unlink()
    destination = raw_dir / f"{chunk_id}.{extension}"
    destination.write_bytes(payload)
    canonical = seg.store_dir(root) / f"{chunk_id}.{seg.CANONICAL_EXTENSION}"
    if canonical.exists():
        canonical.unlink()

    params = read_json(root / "config" / "params.json", {})
    improved_key, translation_key = project_text_keys(params)
    index = seg.load_index(root)
    index.setdefault("segments", {})[chunk_id] = {
        "raw_file": f"raw/{destination.name}",
        "source": seg.SOURCE_RECORDING,
        "status": seg.STATUS_PENDING_INGEST,
        "text_hash": seg.text_hash(seg.chunk_text(chunk, improved_key, translation_key)),
        "bytes": len(payload),
        "created_at": seg.now_iso(),
    }
    seg.save_index(root, index)
    return {"ok": True, "chunk_id": chunk_id, "status": seg.STATUS_PENDING_INGEST, "bytes": len(payload)}


@app.delete("/api/projects/{project_id}/segments/{chunk_id}/audio")
def delete_segment_audio(project_id: str, chunk_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    validate_chunk_id(chunk_id)
    ensure_project_idle(root)
    index = seg.load_index(root)
    index.get("segments", {}).pop(chunk_id, None)
    for directory in (seg.store_dir(root), seg.raw_dir(root)):
        if not directory.exists():
            continue
        for leftover in directory.glob(f"{chunk_id}.*"):
            if leftover.is_file():
                leftover.unlink()
    seg.save_index(root, index)
    return {"ok": True, "chunk_id": chunk_id, "status": seg.STATUS_MISSING}


@app.post("/api/projects/{project_id}/segments/{chunk_id}/regenerate")
def regenerate_segment(project_id: str, chunk_id: str) -> dict[str, Any]:
    root = project_path(project_id)
    validate_chunk_id(chunk_id)
    ensure_project_idle(root)
    _, chunks = load_transcript_chunks(root)
    chunk = find_chunk(chunks, chunk_id)
    params = read_json(root / "config" / "params.json", {})
    improved_key, translation_key = project_text_keys(params)
    if not seg.chunk_text(chunk, improved_key, translation_key):
        raise HTTPException(status_code=400, detail="Chunk has no text to synthesize")
    return queue_voiceover_job(
        root,
        stages="tts",
        job_kind="tts-chunk",
        message=f"Queued regeneration of chunk {chunk_id}",
        chunk_ids=[chunk_id],
    )


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
