"""Local Whisper speech-to-text provider (openai-whisper CLI, no API calls)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .base import SttError, SttProvider, TranscriptionRequest, TranscriptionResult
from .params import DEFAULT_LOCAL_MODEL, stt_param_str
from .schema import (
    CanonicalTranscript,
    TranscriptSegment,
    build_segments_from_words,
    iter_transcript_words,
    make_word,
    words_from_segment_text,
)

WHISPER_PACKAGE = "openai-whisper"
WHISPER_PACKAGE_VERSION = "20231117"


def whisper_is_installed() -> bool:
    return shutil.which("whisper") is not None or importlib.util.find_spec("whisper") is not None


def install_whisper() -> None:
    """Install the whisper CLI on first use, as the previous local path did."""
    if whisper_is_installed():
        return
    command = [sys.executable, "-m", "pip", "install", f"{WHISPER_PACKAGE}=={WHISPER_PACKAGE_VERSION}"]
    try:
        subprocess.check_call(command)
    except subprocess.CalledProcessError as exc:
        raise SttError(f"Failed to install {WHISPER_PACKAGE}=={WHISPER_PACKAGE_VERSION}: {exc}") from exc


class LocalWhisperSttProvider(SttProvider):
    """Transcribes with a locally installed Whisper model."""

    name = "local-whisper"
    required_env = ()

    def resolve_model(self, params: dict[str, Any]) -> str:
        return (
            stt_param_str(params, "stt_model")
            or stt_param_str(params, "stt_local_model")
            or (os.getenv("LOCAL_WHISPER_MODEL") or "").strip()
            or DEFAULT_LOCAL_MODEL
        )

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        logger = request.get_logger()
        params = request.params or {}
        model = self.resolve_model(params)
        logger.info("Transcribing locally with Whisper model %s", model)

        install_whisper()
        data = self.run_whisper(request.audio_path, model, request.language, logger)

        language = str(data.get("language") or "") or request.language
        segments = self.build_segments(data)
        text = str(data.get("text") or "").strip()
        if not text:
            text = " ".join(segment.text for segment in segments).strip()

        transcript = CanonicalTranscript(
            text=text,
            segments=segments,
            provider=self.name,
            model=model,
            language=language,
            duration=segments[-1].end if segments else None,
        )
        return TranscriptionResult(
            transcript=transcript,
            provider_response=self.wrap_provider_response(model, data),
        )

    def run_whisper(
        self,
        audio_path: Path,
        model: str,
        language: str | None,
        logger: Any,
    ) -> dict[str, Any]:
        audio_path = Path(audio_path)
        with tempfile.TemporaryDirectory() as temp_dir:
            command = [
                "whisper",
                "--model", model,
                "--output_format", "json",
                "--word_timestamps", "True",
                "--output_dir", temp_dir,
            ]
            if language:
                command += ["--language", language]
            command.append(str(audio_path))

            logger.info("Running local whisper: %s", " ".join(command))
            try:
                subprocess.run(command, check=True, text=True)
            except FileNotFoundError as exc:
                raise SttError("The 'whisper' command is not available for the local-whisper provider") from exc
            except subprocess.CalledProcessError as exc:
                raise SttError(f"Local whisper failed with exit code {exc.returncode}") from exc

            output_path = Path(temp_dir) / f"{audio_path.stem}.json"
            if not output_path.exists():
                raise SttError(f"Local whisper produced no output at {output_path}")
            with output_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)

    def build_segments(self, data: dict[str, Any]) -> list[TranscriptSegment]:
        """Keep Whisper's own segmentation, backfilling words when timings are missing."""
        raw_segments = data.get("segments") or []
        if not raw_segments:
            return build_segments_from_words(iter_transcript_words(data))

        segments: list[TranscriptSegment] = []
        for index, raw in enumerate(raw_segments):
            segment_words = [word for word in (make_word(entry) for entry in raw.get("words") or []) if word.word]
            if not segment_words:
                segment_words = [
                    make_word(word)
                    for word in words_from_segment_text({"segments": [raw]})
                ]
            start = segment_words[0].start if segment_words else float(raw.get("start", 0.0) or 0.0)
            end = segment_words[-1].end if segment_words else float(raw.get("end", start) or start)
            text = str(raw.get("text") or "").strip() or " ".join(word.word for word in segment_words)
            segments.append(
                TranscriptSegment(
                    id=index,
                    start=start,
                    end=max(start, end),
                    text=text,
                    words=segment_words,
                )
            )
        return segments
