from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from bisect import bisect_right
from pathlib import Path
from typing import Any


DEFAULT_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"
PYANNOTE_SAMPLE_RATE = 16000

DIARIZATION_ENGINES = ("pyannote", "nemotron")
DEFAULT_DIARIZATION_ENGINE = "pyannote"
DEFAULT_NEMOTRON_MODEL = "nvidia/Nemotron-3-Diarization"
NEMOTRON_MAX_SPEAKERS = 8
NEMOTRON_ACTIVITY_THRESHOLD = 0.5
# Shorter speaker runs sandwiched in speech are treated as flicker, not a turn change.
MIN_TURN_SECONDS = 0.3


def diarization_engine() -> str:
    engine = (os.getenv("DIARIZATION_ENGINE") or DEFAULT_DIARIZATION_ENGINE).strip().lower()
    if engine not in DIARIZATION_ENGINES:
        raise RuntimeError(
            f"Unknown DIARIZATION_ENGINE {engine!r}; expected one of: {', '.join(DIARIZATION_ENGINES)}"
        )
    return engine


def diarization_model() -> str:
    if diarization_engine() == "nemotron":
        return os.getenv("NEMOTRON_DIARIZATION_MODEL", "").strip() or DEFAULT_NEMOTRON_MODEL
    return os.getenv("PYANNOTE_MODEL", "").strip() or DEFAULT_PYANNOTE_MODEL


def decode_to_wav(audio_path: Path, destination: Path) -> Path:
    # Pyannote sizes its sliding window from the container header duration, which for MP3
    # overshoots the decodable samples by up to one frame. The final window then comes back
    # short and Audio.crop raises. PCM WAV reports an exact sample count, so it cannot drift.
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "quiet",
        "-i", str(audio_path),
        "-vn",
        "-ac", "1",
        "-ar", str(PYANNOTE_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        "-y",
        str(destination),
    ]
    subprocess.run(command, check=True)
    return destination


def decode_to_samples(audio_path: Path) -> Any:
    import numpy as np

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "quiet",
        "-i", str(audio_path),
        "-vn",
        "-ac", "1",
        "-ar", str(PYANNOTE_SAMPLE_RATE),
        "-f", "f32le",
        "-",
    ]
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE)
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def diarize_speakers(
    audio_path: str | Path,
    number_of_speakers: int,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    active_logger = logger or logging.getLogger(__name__)
    if diarization_engine() == "nemotron":
        return _diarize_with_nemotron(Path(audio_path), number_of_speakers, active_logger)
    return _diarize_with_pyannote(Path(audio_path), number_of_speakers, active_logger)


def _diarize_with_pyannote(
    audio_path: Path,
    number_of_speakers: int,
    active_logger: logging.Logger,
) -> list[dict[str, Any]]:
    model = diarization_model()
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

    with tempfile.TemporaryDirectory(prefix="pyannote-") as workdir:
        wav_path = Path(workdir) / f"{Path(audio_path).stem}.diarization.wav"
        active_logger.info("Decoding %s to %s Hz mono wav for diarization", audio_path, PYANNOTE_SAMPLE_RATE)
        decode_to_wav(Path(audio_path), wav_path)
        output = pipeline(str(wav_path), num_speakers=number_of_speakers)

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


def _torch_device(torch: Any) -> str:
    requested = os.getenv("DIARIZATION_DEVICE", "").strip()
    if requested:
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _diarize_with_nemotron(
    audio_path: Path,
    number_of_speakers: int,
    active_logger: logging.Logger,
) -> list[dict[str, Any]]:
    try:
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "Nemotron diarization requires torch and a transformers build with nemotron3_diarization"
        ) from exc

    model_id = diarization_model()
    device = _torch_device(torch)
    max_speakers = max(1, min(number_of_speakers, NEMOTRON_MAX_SPEAKERS))
    if number_of_speakers > NEMOTRON_MAX_SPEAKERS:
        active_logger.warning(
            "Nemotron diarization tracks at most %s speakers; %s were requested",
            NEMOTRON_MAX_SPEAKERS,
            number_of_speakers,
        )
    active_logger.info(
        "Running Nemotron diarization with model %s on %s for up to %s speakers",
        model_id,
        device,
        max_speakers,
    )
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForAudioFrameClassification.from_pretrained(model_id).to(device).eval()

    active_logger.info("Decoding %s to %s Hz mono samples for diarization", audio_path, PYANNOTE_SAMPLE_RATE)
    samples = decode_to_samples(audio_path)
    inputs = processor(samples, sampling_rate=PYANNOTE_SAMPLE_RATE).to(device, dtype=model.dtype)
    with torch.inference_mode():
        logits = model(**inputs).logits

    probabilities = logits[0].sigmoid().float().cpu().numpy()
    feature_extractor = processor.feature_extractor
    frame_seconds = feature_extractor.hop_length / feature_extractor.sampling_rate
    turns = exclusive_turns_from_probabilities(probabilities, frame_seconds, max_speakers)
    if not turns:
        raise RuntimeError("Nemotron diarization produced no speaker turns")
    active_logger.info("Nemotron diarization produced %s speaker turns", len(turns))
    return turns


def exclusive_turns_from_probabilities(
    probabilities: Any,
    frame_seconds: float,
    max_speakers: int,
) -> list[dict[str, Any]]:
    """One speaker per speech frame, limited to the `max_speakers` who talk the most."""
    import numpy as np

    active = probabilities > NEMOTRON_ACTIVITY_THRESHOLD
    talk_frames = active.sum(axis=0)
    ranked = np.argsort(-talk_frames, kind="stable")[:max_speakers]
    # Channels are in first-arrival order, so sorting keeps SPEAKER_00 as the first voice heard.
    kept = sorted(int(channel) for channel in ranked if talk_frames[channel] > 0)
    if not kept:
        return []

    speech = active.any(axis=1)
    labels = np.where(speech, np.argmax(probabilities[:, kept], axis=1), -1)
    boundaries = np.flatnonzero(np.diff(labels)) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [len(labels)]))

    min_frames = max(1, round(MIN_TURN_SECONDS / frame_seconds))
    runs: list[list[int]] = []
    for start, end in zip(starts.tolist(), ends.tolist()):
        label = int(labels[start])
        if label < 0:
            continue
        if runs and runs[-1][1] == start and (runs[-1][2] == label or end - start < min_frames):
            runs[-1][1] = end
            continue
        runs.append([start, end, label])

    return [
        {
            "start": round(start * frame_seconds, 3),
            "end": round(end * frame_seconds, 3),
            "speaker": f"SPEAKER_{label:02d}",
        }
        for start, end, label in runs
    ]


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
