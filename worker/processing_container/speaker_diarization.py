from __future__ import annotations

import logging
import os
from bisect import bisect_right
from pathlib import Path
from typing import Any


DEFAULT_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"


def diarize_speakers(
    audio_path: str | Path,
    number_of_speakers: int,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    active_logger = logger or logging.getLogger(__name__)
    model = os.getenv("PYANNOTE_MODEL", DEFAULT_PYANNOTE_MODEL).strip() or DEFAULT_PYANNOTE_MODEL
    model_path = Path(model).expanduser()
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not model_path.exists() and not token:
        raise RuntimeError(
            "Speaker recognition requires HF_TOKEN (or HUGGINGFACE_TOKEN). "
            "Accept the pyannote Community-1 model terms on Hugging Face first."
        )

    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError("Speaker recognition requires the pyannote.audio package") from exc

    model_source = str(model_path) if model_path.exists() else model
    active_logger.info(
        "Running local pyannote diarization with model %s and %s speakers",
        model_source,
        number_of_speakers,
    )
    pipeline = Pipeline.from_pretrained(model_source, token=token)
    if pipeline is None:
        raise RuntimeError(f"Could not load pyannote model: {model_source}")

    output = pipeline(str(audio_path), num_speakers=number_of_speakers)
    annotation = output.exclusive_speaker_diarization
    turns = [
        {
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": str(speaker),
        }
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda item: (item["start"], item["end"]))
    if not turns:
        raise RuntimeError("Pyannote diarization produced no speaker turns")
    active_logger.info("Pyannote diarization produced %s speaker turns", len(turns))
    return turns


def assign_speakers_to_words(
    words: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not turns:
        return [dict(word) for word in words]

    sorted_turns = sorted(turns, key=lambda item: (float(item["start"]), float(item["end"])))
    starts = [float(turn["start"]) for turn in sorted_turns]
    assigned: list[dict[str, Any]] = []

    for word in words:
        item = dict(word)
        start = float(item.get("start", item.get("end", 0.0)))
        end = float(item.get("end", start))
        midpoint = (start + end) / 2.0
        previous_index = bisect_right(starts, midpoint) - 1
        previous_candidate = max(0, min(previous_index, len(sorted_turns) - 1))
        next_candidate = max(0, min(previous_index + 1, len(sorted_turns) - 1))
        candidate_indices = [previous_candidate]
        if next_candidate != previous_candidate:
            candidate_indices.append(next_candidate)

        def distance_from_midpoint(index: int) -> tuple[float, float]:
            turn = sorted_turns[index]
            turn_start = float(turn["start"])
            turn_end = float(turn["end"])
            if turn_start <= midpoint <= turn_end:
                distance = 0.0
            else:
                distance = min(abs(midpoint - turn_start), abs(midpoint - turn_end))
            return distance, turn_start

        nearest_index = min(candidate_indices, key=distance_from_midpoint)
        item["speaker"] = str(sorted_turns[nearest_index]["speaker"])
        assigned.append(item)

    return assigned
