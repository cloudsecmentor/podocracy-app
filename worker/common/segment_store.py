"""Per-chunk voiceover segment store.

Audio belongs to a chunk, not to a pipeline run. Every chunk in `*.improved.json`
carries a stable `chunk_id`, and the store keeps exactly one canonical audio file
per id under `<project>/work/segments/`. Synthesis fills gaps in the store, the
browser can overwrite any single entry with a recording, and assembly reads the
store without caring which of the two produced a given segment.

Timings are user-editable, so they cannot be the key; `chunk_id` survives
retiming, reordering, and text edits. Assembly still needs the legacy
`<start>-<end>.ogg` filenames, so the build stage stages copies under those names
rather than storing them that way.

Pure-stdlib on purpose: `apps/app-api/segments.py` mirrors the parts the portal
API needs, and `worker/test_segment_store.py` asserts the two stay in agreement.
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
# What the browser's MediaRecorder can produce plus the formats the legacy
# custom-recording path accepted.
RAW_UPLOAD_EXTENSIONS = ("webm", "ogg", "oga", "m4a", "mp4", "mp3", "wav")

# Params that change how a chunk sounds. A change to any of them makes generated
# audio stale; recordings are deliberately unaffected.
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
    """Give every chunk a stable id in place. Returns True when anything changed.

    Existing valid ids are kept. Duplicates keep the first occurrence and the rest
    are reassigned, because two chunks sharing an id would fight over one file.
    Ids are allocated above the highest in use so a deleted id is never recycled.
    """
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


def staging_basename(chunk: dict[str, Any]) -> str:
    """Legacy assembly filename stem: `0738-0749`, or `0738` when there is no end.

    An `end` of `""` counts as absent. The editor writes empty strings for fields
    the transcript never had, and `0738-.ogg` would fail the assembler's filename
    check and be silently dropped from the mix.
    """
    start = str(chunk.get("start") or "").strip()
    end = str(chunk.get("end") or "").strip()
    if not start:
        raise ValueError(f"Chunk {chunk.get('chunk_id')} has no start time")
    return f"{start}-{end}" if end else start


def store_dir(project_root: Path | str) -> Path:
    return Path(project_root) / "work" / "segments"


def raw_dir(project_root: Path | str) -> Path:
    return store_dir(project_root) / "raw"


def orphan_dir(project_root: Path | str) -> Path:
    return store_dir(project_root) / "orphaned"


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
    """Atomic, because the portal API and the worker both write this file."""
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
    """Canonical file if it exists, else the not-yet-ingested upload."""
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
    # A recording is the user's own work and cannot be reproduced by changing a
    # voice setting, so voice params never invalidate it.
    if entry.get("source") == SOURCE_RECORDING:
        return STATUS_READY
    if entry.get("voice_hash") != current_voice_hash:
        return STATUS_STALE
    return STATUS_READY


def needs_synthesis(status: str, entry: dict[str, Any] | None) -> bool:
    """Recordings are never regenerated automatically; `--chunks` bypasses this."""
    if entry and entry.get("source") == SOURCE_RECORDING:
        return False
    return status in (STATUS_MISSING, STATUS_STALE, STATUS_FAILED)


def audio_block(entry: dict[str, Any] | None, status: str) -> dict[str, Any] | None:
    """The `audio` object mirrored onto the chunk in the improved JSON.

    Paths are project-relative here and store-relative in `segments.json`, so the
    transcript stays readable on its own.
    """
    if not entry:
        return None
    block: dict[str, Any] = {
        "file": f"work/segments/{entry['file']}" if entry.get("file") else None,
        "source": entry.get("source"),
        "engine": entry.get("engine"),
        "voice": entry.get("voice"),
        "model": entry.get("model"),
        "duration_ms": entry.get("duration_ms"),
        "text_hash": entry.get("text_hash"),
        "generated_at": entry.get("created_at"),
        "status": status,
    }
    if entry.get("error"):
        block["error"] = entry["error"]
    return {key: value for key, value in block.items() if value is not None}


def describe_chunks(
    project_root: Path | str,
    chunks: Iterable[dict[str, Any]],
    params: dict[str, Any],
    *,
    improved_key: str = "imp",
    translation_key: str = "dltrans",
) -> list[dict[str, Any]]:
    """Status of every chunk in one pass, for the editor and the worker alike."""
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


def probe_duration_ms(path: Path) -> int | None:
    """Best effort: a missing duration degrades the UI, it does not break a build."""
    try:
        from pydub import AudioSegment
    except ImportError:
        return None
    try:
        return int(len(AudioSegment.from_file(path)))
    except Exception:
        return None
