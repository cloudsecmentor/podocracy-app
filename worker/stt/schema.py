"""Canonical transcript schema shared by every speech-to-text provider.

Every provider returns the same document, so the stages after transcription
(combine, timesync, translate, improve, voiceover) never learn which engine
produced the words.

The canonical document looks like this::

    {
      "schema_version": "podocracy-transcript-v1",
      "provider": "openai",
      "model": "whisper-1",
      "language": "en",
      "duration": 91.2,
      "text": "Full transcript text ...",
      "words": [{"word": "Hello", "start": 0.0, "end": 0.42, "speaker": "SPEAKER_00"}],
      "segments": [
        {"id": 0, "start": 0.0, "end": 4.2, "text": "Hello ...", "speaker": "SPEAKER_00",
         "words": [...]}
      ]
    }

``words`` is the authoritative, time-ordered word list; ``segments[].words``
holds the same word objects grouped by segment, which is the shape the existing
combine stage already reads. Untouched provider payloads are never mixed in
here - they are written to their own file next to the normalized transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TRANSCRIPT_SCHEMA_VERSION = "podocracy-transcript-v1"

# Written next to the normalized transcript so the untouched provider payload
# stays inspectable without polluting the canonical document.
PROVIDER_RESPONSE_SCHEMA_VERSION = "podocracy-stt-provider-response-v1"


class TranscriptSchemaError(ValueError):
    """Raised when a transcript does not satisfy the canonical schema."""


def _coerce_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise TranscriptSchemaError(f"{field_name} must be a number, got {value!r}") from exc
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise TranscriptSchemaError(f"{field_name} must be a finite number, got {value!r}")
    return number


@dataclass
class TranscriptWord:
    word: str
    start: float
    end: float
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "word": self.word,
            "start": self.start,
            "end": self.end,
        }
        if self.speaker is not None:
            item["speaker"] = self.speaker
        return item


@dataclass
class TranscriptSegment:
    id: int
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
        }
        if self.speaker is not None:
            item["speaker"] = self.speaker
        return item


@dataclass
class CanonicalTranscript:
    """Provider-independent transcript with provenance."""

    text: str
    segments: list[TranscriptSegment]
    provider: str
    model: str
    language: str | None = None
    duration: float | None = None
    provider_response_path: str | None = None

    @property
    def words(self) -> list[TranscriptWord]:
        return [word for segment in self.segments for word in segment.words]

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": TRANSCRIPT_SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "duration": self.duration,
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
            "segments": [segment.to_dict() for segment in self.segments],
        }
        if self.provider_response_path:
            document["provider_response_path"] = self.provider_response_path
        return document


def build_segments_from_words(
    words: list[dict[str, Any]],
    *,
    max_gap_seconds: float = 2.0,
    max_chars: int = 700,
) -> list[TranscriptSegment]:
    """Group a flat word list into segments when the provider gives none.

    Only used for providers that return words without segmentation; providers
    that do return segments keep their own boundaries.
    """
    segments: list[TranscriptSegment] = []
    current: list[TranscriptWord] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        speakers = {word.speaker for word in current}
        segments.append(
            TranscriptSegment(
                id=len(segments),
                start=current[0].start,
                end=current[-1].end,
                text=" ".join(word.word for word in current).strip(),
                words=list(current),
                speaker=current[0].speaker if len(speakers) == 1 else None,
            )
        )
        current = []
        current_chars = 0

    previous_end: float | None = None
    previous_speaker: Any = object()
    for entry in words:
        word = make_word(entry)
        if not word.word:
            continue
        gap = word.start - previous_end if previous_end is not None else 0.0
        if current and (gap > max_gap_seconds or current_chars >= max_chars or word.speaker != previous_speaker):
            flush()
        current.append(word)
        current_chars += len(word.word) + 1
        previous_end = word.end
        previous_speaker = word.speaker
    flush()
    return segments


def make_word(entry: Any) -> TranscriptWord:
    """Build a canonical word from a dict or an SDK word object."""
    if isinstance(entry, TranscriptWord):
        return entry
    if isinstance(entry, dict):
        raw_word = entry.get("word", entry.get("text", ""))
        start = entry.get("start")
        end = entry.get("end")
        speaker = entry.get("speaker")
    else:
        raw_word = getattr(entry, "word", getattr(entry, "text", ""))
        start = getattr(entry, "start", None)
        end = getattr(entry, "end", None)
        speaker = getattr(entry, "speaker", None)
    if start is None:
        start = end
    if end is None:
        end = start
    return TranscriptWord(
        word=str(raw_word).strip(),
        start=_coerce_float(start, "word.start"),
        end=_coerce_float(end, "word.end"),
        speaker=str(speaker) if speaker is not None else None,
    )


def is_canonical_transcript(transcript: Any) -> bool:
    return isinstance(transcript, dict) and transcript.get("schema_version") == TRANSCRIPT_SCHEMA_VERSION


def iter_transcript_words(transcript: dict[str, Any], offset_seconds: float = 0.0) -> list[dict[str, Any]]:
    """Read the word list out of a canonical transcript or a legacy raw file.

    Canonical documents carry the words twice (flat and grouped per segment),
    so the flat list wins to avoid counting them twice. Legacy Whisper-API and
    local-Whisper raw files only fill one of the two, so both still work.
    """
    raw_words: list[Any] = list(transcript.get("words") or [])
    if not raw_words:
        for segment in transcript.get("segments") or []:
            raw_words.extend(segment.get("words") or [])

    words: list[dict[str, Any]] = []
    for entry in raw_words:
        word = make_word(entry)
        if not word.word:
            continue
        item = word.to_dict()
        item["start"] = word.start + offset_seconds
        item["end"] = word.end + offset_seconds
        words.append(item)
    return words


def words_from_segment_text(transcript: dict[str, Any], offset_seconds: float = 0.0) -> list[dict[str, Any]]:
    """Spread segment text evenly over its duration when word timings are absent."""
    words: list[dict[str, Any]] = []
    for segment in transcript.get("segments") or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _coerce_float(segment.get("start", 0.0), "segment.start") + offset_seconds
        end = _coerce_float(segment.get("end", segment.get("start", 0.0)), "segment.end") + offset_seconds
        parts = [part for part in text.split() if part]
        duration = max(0.0, end - start)
        step = duration / len(parts) if parts else 0.0
        for index, word in enumerate(parts):
            words.append(
                {
                    "word": word,
                    "start": start + index * step,
                    "end": start + (index + 1) * step if index < len(parts) - 1 else end,
                }
            )
    return words


def replace_transcript_words(transcript: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    """Write an updated word list back, keeping flat and per-segment views in sync.

    ``words`` must be the list previously read with :func:`iter_transcript_words`,
    in the same order and length - diarization only annotates words, it never
    adds or drops them.
    """
    segments = transcript.get("segments") or []
    grouped_count = sum(len(segment.get("words") or []) for segment in segments)
    has_flat = bool(transcript.get("words"))

    if has_flat:
        transcript["words"] = words

    if grouped_count == len(words) and grouped_count:
        cursor = 0
        for segment in segments:
            size = len(segment.get("words") or [])
            segment["words"] = words[cursor : cursor + size]
            cursor += size
    elif not has_flat and segments:
        # Legacy shape with a single catch-all segment.
        segments[0]["words"] = words
    elif not has_flat:
        transcript["words"] = words
    return transcript


def validate_transcript(transcript: Any) -> dict[str, Any]:
    """Validate a canonical transcript document, returning it unchanged."""
    if not isinstance(transcript, dict):
        raise TranscriptSchemaError("Transcript must be a JSON object")

    version = transcript.get("schema_version")
    if version != TRANSCRIPT_SCHEMA_VERSION:
        raise TranscriptSchemaError(
            f"Unsupported transcript schema_version {version!r}, expected {TRANSCRIPT_SCHEMA_VERSION!r}"
        )

    for key in ("provider", "model"):
        value = transcript.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TranscriptSchemaError(f"Transcript {key} must be a non-empty string, got {value!r}")

    language = transcript.get("language")
    if language is not None and not isinstance(language, str):
        raise TranscriptSchemaError(f"Transcript language must be a string or null, got {language!r}")

    duration = transcript.get("duration")
    if duration is not None:
        _coerce_float(duration, "duration")

    if not isinstance(transcript.get("text"), str):
        raise TranscriptSchemaError("Transcript text must be a string")

    words = transcript.get("words")
    if not isinstance(words, list):
        raise TranscriptSchemaError("Transcript words must be a list")
    for index, word in enumerate(words):
        _validate_word(word, f"words[{index}]")

    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise TranscriptSchemaError("Transcript segments must be a list")

    grouped = 0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise TranscriptSchemaError(f"segments[{index}] must be an object")
        if not isinstance(segment.get("id"), int) or isinstance(segment.get("id"), bool):
            raise TranscriptSchemaError(f"segments[{index}].id must be an integer")
        start = _coerce_float(segment.get("start"), f"segments[{index}].start")
        end = _coerce_float(segment.get("end"), f"segments[{index}].end")
        if end < start:
            raise TranscriptSchemaError(f"segments[{index}] ends before it starts")
        if not isinstance(segment.get("text"), str):
            raise TranscriptSchemaError(f"segments[{index}].text must be a string")
        segment_words = segment.get("words")
        if not isinstance(segment_words, list):
            raise TranscriptSchemaError(f"segments[{index}].words must be a list")
        for word_index, word in enumerate(segment_words):
            _validate_word(word, f"segments[{index}].words[{word_index}]")
        grouped += len(segment_words)

    if grouped != len(words):
        raise TranscriptSchemaError(
            f"Transcript has {len(words)} words but {grouped} grouped into segments"
        )
    return transcript


def _validate_word(word: Any, field_name: str) -> None:
    if not isinstance(word, dict):
        raise TranscriptSchemaError(f"{field_name} must be an object")
    text = word.get("word")
    if not isinstance(text, str) or not text.strip():
        raise TranscriptSchemaError(f"{field_name}.word must be a non-empty string")
    start = _coerce_float(word.get("start"), f"{field_name}.start")
    end = _coerce_float(word.get("end"), f"{field_name}.end")
    if end < start:
        raise TranscriptSchemaError(f"{field_name} ends before it starts")
    speaker = word.get("speaker")
    if speaker is not None and not isinstance(speaker, str):
        raise TranscriptSchemaError(f"{field_name}.speaker must be a string or absent")
