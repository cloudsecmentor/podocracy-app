"""Parameter lookup for the speech-to-text boundary, with legacy fallbacks.

Project params files written before the provider boundary existed use
Whisper-specific keys. Every new ``stt_*`` key therefore falls back to its
Whisper-era name, so a project queued by an older build keeps running.
"""

from __future__ import annotations

from typing import Any

# New canonical key -> legacy key(s) still accepted, most recent first.
LEGACY_PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "stt_chunk_length_sec": ("whisper_chunk_length_sec",),
    "stt_silence_split": ("whisper_silence_split",),
    "stt_silence_sec": ("whisper_silence_sec",),
    "stt_max_upload_size_mb": ("whisper_api_max_size_mp3",),
    "stt_model": ("whisper_model",),
    "stt_local_model": ("whisper_default_model_local",),
}

DEFAULT_CHUNK_LENGTH_SEC = 300
DEFAULT_SILENCE_SEC = 2.0
DEFAULT_MAX_UPLOAD_SIZE_MB = 24.0
DEFAULT_LOCAL_MODEL = "large"
DEFAULT_WINDOW_SIZE_FOR_TIMESYNC = 5


def parse_legacy_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _raw_value(params: dict[str, Any], key: str) -> Any:
    """Return the value for ``key``, or the first legacy alias that is set."""
    for candidate in (key, *LEGACY_PARAM_ALIASES.get(key, ())):
        if candidate not in params:
            continue
        value = params[candidate]
        # parameters.json stores {"value": ..., "description": ...} entries.
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        if value is None or value == "":
            continue
        return value
    return None


def stt_param(params: dict[str, Any], key: str, default: Any = None) -> Any:
    value = _raw_value(params or {}, key)
    return default if value is None else value


def stt_param_int(params: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(stt_param(params, key, default))
    except (TypeError, ValueError):
        return default


def stt_param_float(params: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(stt_param(params, key, default))
    except (TypeError, ValueError):
        return default


def stt_param_bool(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = _raw_value(params or {}, key)
    return default if value is None else parse_legacy_bool(value)


def stt_param_str(params: dict[str, Any], key: str, default: str = "") -> str:
    value = stt_param(params, key, default)
    return str(value).strip() if value is not None else default
