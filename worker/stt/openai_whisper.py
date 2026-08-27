"""OpenAI speech-to-text provider."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .alignment import add_start_end_times_to_transcript, fill_missing_timings, tokenize_text
from .base import SttProvider, TranscriptionRequest, TranscriptionResult
from .chunking import split_audio_ranges
from .params import (
    DEFAULT_WINDOW_SIZE_FOR_TIMESYNC,
    stt_param_bool,
    stt_param_int,
    stt_param_str,
)
from .schema import (
    CanonicalTranscript,
    build_segments_from_words,
    iter_transcript_words,
    words_from_segment_text,
)

DEFAULT_MODEL = "whisper-1"


def model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {"text": str(value)}


class OpenAiSttProvider(SttProvider):
    """Transcribes with the OpenAI audio transcriptions API."""

    name = "openai"
    required_env = ("OPENAI_API_KEY",)

    def resolve_model(self, params: dict[str, Any]) -> str:
        return (
            stt_param_str(params, "stt_model")
            or (os.getenv("OPENAI_TRANSCRIBE_MODEL") or "").strip()
            or DEFAULT_MODEL
        )

    def build_client(self):
        from openai import OpenAI

        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.ensure_credentials()
        logger = request.get_logger()
        params = request.params or {}
        model = self.resolve_model(params)
        detailed = stt_param_bool(params, "detailed_transcription", True)
        window_size = stt_param_int(params, "window_size_for_timesync", DEFAULT_WINDOW_SIZE_FOR_TIMESYNC)
        logger.info("Transcribing with OpenAI model %s detailed=%s", model, detailed)

        from pydub import AudioSegment

        client = self.build_client()
        audio = AudioSegment.from_file(request.audio_path)
        ranges = split_audio_ranges(audio, params, logger, audio_path=request.audio_path)
        chunk_dir = request.chunk_dir()

        chunk_responses: list[dict[str, Any]] = []
        words: list[dict[str, Any]] = []
        text_parts: list[str] = []
        language: str | None = request.language
        duration = audio.duration_seconds

        for index, (start_ms, end_ms) in enumerate(ranges):
            chunk_path = Path(chunk_dir) / f"chunk-{index:04d}-{start_ms}-{end_ms}.mp3"
            audio[start_ms:end_ms].export(chunk_path, format="mp3", bitrate="192k")
            data = self.transcribe_chunk(client, chunk_path, model, detailed, request.language)
            offset_seconds = start_ms / 1000.0
            chunk_responses.append(
                {"index": index, "start_ms": start_ms, "end_ms": end_ms, "response": data}
            )
            text_parts.append(str(data.get("text") or "").strip())
            if not language and data.get("language"):
                language = str(data["language"])

            chunk_words = self.words_for_chunk(data, detailed, window_size, offset_seconds)
            words.extend(chunk_words)
            logger.info("Transcribed chunk %s/%s with %s words", index + 1, len(ranges), len(chunk_words))

        text = " ".join(part for part in text_parts if part).strip()
        segments = build_segments_from_words(words)
        transcript = CanonicalTranscript(
            text=text,
            segments=segments,
            provider=self.name,
            model=model,
            language=language,
            duration=duration,
        )
        return TranscriptionResult(
            transcript=transcript,
            provider_response=self.wrap_provider_response(model, {"chunks": chunk_responses}),
        )

    def transcribe_chunk(
        self,
        client: Any,
        chunk_path: Path,
        model: str,
        detailed: bool,
        language: str | None,
    ) -> dict[str, Any]:
        granularities = ["word"] if detailed else ["segment"]
        with Path(chunk_path).open("rb") as audio_file:
            try:
                transcript = client.audio.transcriptions.create(
                    model=model,
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=granularities,
                    language=language,
                )
            except TypeError:
                # Older SDKs (and some proxies) reject the granularity/language kwargs.
                audio_file.seek(0)
                transcript = client.audio.transcriptions.create(
                    model=model,
                    file=audio_file,
                    response_format="verbose_json",
                )
        return model_dump(transcript)

    def words_for_chunk(
        self,
        data: dict[str, Any],
        detailed: bool,
        window_size: int,
        offset_seconds: float,
    ) -> list[dict[str, Any]]:
        """Punctuated words with numeric timings for one audio chunk.

        The API returns punctuated text and unpunctuated timed words, so the
        text is tokenized and re-timed against the word list; without that the
        combine stage has no punctuation to split sentences on.
        """
        timed_words = iter_transcript_words(data) if detailed else []
        text = str(data.get("text") or "").strip()
        tokens = tokenize_text(text)

        if timed_words and tokens:
            aligned = add_start_end_times_to_transcript(
                [{"word": token} for token in tokens],
                timed_words,
                window_size,
            )
            words = fill_missing_timings(aligned, fallback_start=0.0)
        elif timed_words:
            words = timed_words
        else:
            words = words_from_segment_text(data)

        shifted: list[dict[str, Any]] = []
        for word in words:
            if not str(word.get("word") or "").strip():
                continue
            item = dict(word)
            item["start"] = float(item["start"]) + offset_seconds
            item["end"] = float(item["end"]) + offset_seconds
            shifted.append(item)
        return shifted
