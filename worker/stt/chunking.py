"""Audio splitting for providers that upload audio in pieces.

Ported from ``pd-010-02-whisper-api-transcribe.py`` and ``local_worker`` so both
pipelines split identically: a file that fits the provider upload limit is sent
in one request, anything larger is cut into fixed-length pieces, and silence
splitting is used instead when the project asks for it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .params import (
    DEFAULT_CHUNK_LENGTH_SEC,
    DEFAULT_MAX_UPLOAD_SIZE_MB,
    DEFAULT_SILENCE_SEC,
    stt_param_bool,
    stt_param_float,
    stt_param_int,
)

SILENCE_THRESHOLD_DB = -40


def fixed_ranges(total_ms: int, max_chunk_ms: int) -> list[tuple[int, int]]:
    return [(start, min(start + max_chunk_ms, total_ms)) for start in range(0, total_ms, max_chunk_ms)]


def split_audio_ranges(
    audio: Any,
    params: dict[str, Any],
    logger: logging.Logger | None = None,
    audio_path: str | Path | None = None,
) -> list[tuple[int, int]]:
    """Return (start_ms, end_ms) ranges covering the whole audio."""
    log = logger or logging.getLogger("podocracy.stt")
    chunk_length_sec = stt_param_int(params, "stt_chunk_length_sec", DEFAULT_CHUNK_LENGTH_SEC)
    silence_sec = stt_param_float(params, "stt_silence_sec", DEFAULT_SILENCE_SEC)
    use_silence = stt_param_bool(params, "stt_silence_split", False)
    max_upload_bytes = int(stt_param_float(params, "stt_max_upload_size_mb", DEFAULT_MAX_UPLOAD_SIZE_MB) * 1024 * 1024)
    max_chunk_ms = max(1000, chunk_length_sec * 1000)
    min_chunk_ms = max(500, int(0.5 * max_chunk_ms))
    total_ms = len(audio)

    if not use_silence:
        file_size = os.path.getsize(audio_path) if audio_path and os.path.exists(audio_path) else None
        if file_size is not None and file_size <= max_upload_bytes:
            log.info("Audio is %s bytes, within the %s byte upload limit: sending as one chunk", file_size, max_upload_bytes)
            return [(0, total_ms)]
        if file_size is None and total_ms <= max_chunk_ms:
            return [(0, total_ms)]
        log.info("Splitting transcription audio into %ss chunks", chunk_length_sec)
        return fixed_ranges(total_ms, max_chunk_ms) or [(0, total_ms)]

    log.info("Splitting transcription audio on silence: chunk=%ss silence=%ss", chunk_length_sec, silence_sec)
    from pydub import silence as pydub_silence

    silence_segments = pydub_silence.detect_silence(
        audio,
        min_silence_len=int(silence_sec * 1000),
        silence_thresh=SILENCE_THRESHOLD_DB,
    )
    if not silence_segments:
        return fixed_ranges(total_ms, max_chunk_ms) or [(0, total_ms)]

    ranges: list[tuple[int, int]] = []
    current_position = 0
    for start, end in silence_segments:
        middle = (start + end) // 2
        chunk_length = middle - current_position
        if min_chunk_ms <= chunk_length <= max_chunk_ms:
            ranges.append((current_position, middle))
            current_position = middle
        elif chunk_length > max_chunk_ms:
            while current_position + max_chunk_ms < middle:
                next_position = current_position + max_chunk_ms
                ranges.append((current_position, next_position))
                current_position = next_position
            if current_position < middle:
                ranges.append((current_position, middle))
                current_position = middle

    while current_position + max_chunk_ms < total_ms:
        next_position = current_position + max_chunk_ms
        ranges.append((current_position, next_position))
        current_position = next_position
    if current_position < total_ms:
        ranges.append((current_position, total_ms))
    return ranges or [(0, total_ms)]
