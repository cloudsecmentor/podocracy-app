"""Portal-side view of the per-chunk voiceover segment store.

Canonical implementation: `worker/common/segment_store.py`. The two images are
built from separate Docker contexts (`./apps/app-api` and `./worker`), so this is
a deliberate copy of the parts the portal needs rather than a shared import.
`worker/test_segment_store.py` loads both files and asserts they agree, so drift
fails the test suite instead of surfacing as a mislabelled chunk in the editor.

Only read-side logic plus upload bookkeeping lives here. Synthesis, audio
conversion, duration probing and assembly stay in the worker, which is the image
that has ffmpeg.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SEGMENT_STORE_VERSION = 1

CHUNK_ID_RE = re.compile(r"^c\d{3,}$")

STATUS_READY = "ready"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_PENDING_INGEST = "pending_ingest"

SOURCE_TTS = "tts"
SOURCE_RECORDING = "recording"

CANONICAL_EXTENSION = "ogg"
RAW_UPLOAD_EXTENSIONS = ("webm", "ogg", "oga", "m4a", "mp4", "mp3", "wav")

VOICE_PARAM_KEYS = (
    "tts_api",
    "voice",
    "vibevoice_model",
    "vibevoice_speed",
    "vibevoice_cfg_scale",
    "openai_model_tts",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_chunk_id(ordinal: int) -> str:
    return f"c{ordinal:03d}"


def is_chunk_id(value: Any) -> bool:
    return isinstance(value, str) and bool(CHUNK_ID_RE.match(value))


def assign_chunk_ids(chunks: list[dict[str, Any]]) -> bool:
    taken: set[str] = set()
    highest = -1
    keep: list[bool] = []
    for chunk in chunks:
        candidate = chunk.get("chunk_id")
        usable = is_chunk_id(candidate) and candidate not in taken
        if usable:
            taken.add(candidate)
            highest = max(highest, int(candidate[1:]))
        keep.append(usable)

    changed = False
    next_ordinal = highest + 1
    for chunk, usable in zip(chunks, keep):
        if usable:
            continue
        while format_chunk_id(next_ordinal) in taken:
            next_ordinal += 1
        new_id = format_chunk_id(next_ordinal)
        chunk["chunk_id"] = new_id
        taken.add(new_id)
        next_ordinal += 1
        changed = True
    return changed


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def text_hash(text: Any) -> str:
    return _digest(str(text or "").strip())


def voice_hash(params: dict[str, Any]) -> str:
    payload = {key: str(params.get(key) or "") for key in VOICE_PARAM_KEYS}
    return _digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def chunk_text(chunk: dict[str, Any], improved_key: str = "imp", translation_key: str = "dltrans") -> str:
    """Text to voice for this chunk, or "" when it should stay silent.

    A present-but-blank improved key is how the editor silences a chunk, so it
    must not fall through. A missing key instead means the improve stage never
    ran, and the translation is the best text available.
    """
    if improved_key in chunk:
        return str(chunk.get(improved_key) or "").strip()
    return str(chunk.get(translation_key) or "").strip()


def store_dir(project_root: Path | str) -> Path:
    return Path(project_root) / "work" / "segments"


def raw_dir(project_root: Path | str) -> Path:
    return store_dir(project_root) / "raw"


def index_path(project_root: Path | str) -> Path:
    return store_dir(project_root) / "segments.json"


def empty_index() -> dict[str, Any]:
    return {"version": SEGMENT_STORE_VERSION, "segments": {}}


def load_index(project_root: Path | str) -> dict[str, Any]:
    path = index_path(project_root)
    if not path.exists():
        return empty_index()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return empty_index()
    if not isinstance(data, dict) or not isinstance(data.get("segments"), dict):
        return empty_index()
    data.setdefault("version", SEGMENT_STORE_VERSION)
    return data


def save_index(project_root: Path | str, index: dict[str, Any]) -> None:
    path = index_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f".json.tmp.{os.getpid()}")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def segment_entry(index: dict[str, Any], chunk_id: str) -> dict[str, Any] | None:
    entry = index.get("segments", {}).get(chunk_id)
    return entry if isinstance(entry, dict) else None


def audio_path(project_root: Path | str, entry: dict[str, Any] | None) -> Path | None:
    if not entry:
        return None
    for key in ("file", "raw_file"):
        relative = entry.get(key)
        if not relative:
            continue
        candidate = store_dir(project_root) / relative
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def segment_status(
    entry: dict[str, Any] | None,
    *,
    text: str,
    current_text_hash: str,
    current_voice_hash: str,
    audio_exists: bool,
) -> str:
    if not text.strip():
        return STATUS_SKIPPED
    if not entry:
        return STATUS_MISSING
    stored = entry.get("status")
    if stored == STATUS_FAILED:
        return STATUS_FAILED
    if stored == STATUS_PENDING_INGEST:
        return STATUS_PENDING_INGEST
    if not audio_exists:
        return STATUS_MISSING
    if entry.get("text_hash") != current_text_hash:
        return STATUS_STALE
    if entry.get("source") == SOURCE_RECORDING:
        return STATUS_READY
    if entry.get("voice_hash") != current_voice_hash:
        return STATUS_STALE
    return STATUS_READY


def describe_chunks(
    project_root: Path | str,
    chunks: Iterable[dict[str, Any]],
    params: dict[str, Any],
    *,
    improved_key: str = "imp",
    translation_key: str = "dltrans",
) -> list[dict[str, Any]]:
    index = load_index(project_root)
    current_voice_hash = voice_hash(params)
    described = []
    for position, chunk in enumerate(chunks):
        chunk_id = chunk.get("chunk_id")
        text = chunk_text(chunk, improved_key, translation_key)
        entry = segment_entry(index, chunk_id) if chunk_id else None
        resolved = audio_path(project_root, entry)
        status = segment_status(
            entry,
            text=text,
            current_text_hash=text_hash(text),
            current_voice_hash=current_voice_hash,
            audio_exists=resolved is not None,
        )
        described.append(
            {
                "chunk_id": chunk_id,
                "index": position,
                "start": str(chunk.get("start") or ""),
                "end": str(chunk.get("end") or ""),
                "speaker": str(chunk.get("speaker") or ""),
                "status": status,
                "source": (entry or {}).get("source"),
                "engine": (entry or {}).get("engine"),
                "voice": (entry or {}).get("voice"),
                "duration_ms": (entry or {}).get("duration_ms"),
                "bytes": (entry or {}).get("bytes"),
                "error": (entry or {}).get("error"),
                "has_audio": resolved is not None,
            }
        )
    return described
