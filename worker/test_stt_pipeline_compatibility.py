"""The stages after transcription must not care which provider produced the words."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # pydub is a container-only dependency; local_worker imports it at module level.
    import pydub  # noqa: F401
except ModuleNotFoundError:
    pydub_stub = types.ModuleType("pydub")
    pydub_stub.AudioSegment = object
    pydub_stub.silence = types.ModuleType("pydub.silence")
    sys.modules["pydub"] = pydub_stub
    sys.modules["pydub.silence"] = pydub_stub.silence

import local_worker
from stt import (
    CanonicalTranscript,
    TranscriptionResult,
    TranscriptSegment,
    TranscriptWord,
    iter_transcript_words,
    validate_transcript,
)

LOGGER = logging.getLogger("test")

WORDS = [
    ("Hello", 0.0, 0.4),
    ("there", 0.4, 0.9),
    (",", 0.9, 0.9),
    ("friend", 0.9, 1.4),
    (".", 1.4, 1.4),
    ("This", 1.6, 1.9),
    ("is", 1.9, 2.1),
    ("a", 2.1, 2.2),
    ("test", 2.2, 2.8),
    (".", 2.8, 2.8),
]
TEXT = "Hello there, friend. This is a test."


def canonical_transcript(provider: str, model: str, speaker: str | None = None) -> CanonicalTranscript:
    words = [TranscriptWord(word=word, start=start, end=end, speaker=speaker) for word, start, end in WORDS]
    return CanonicalTranscript(
        text=TEXT,
        segments=[
            TranscriptSegment(id=0, start=0.0, end=1.4, text="Hello there, friend.", words=words[:5], speaker=speaker),
            TranscriptSegment(id=1, start=1.6, end=2.8, text="This is a test.", words=words[5:], speaker=speaker),
        ],
        provider=provider,
        model=model,
        language="en",
        duration=2.8,
    )


LEGACY_WHISPER_API_RAW = {
    # Shape written by the pre-boundary whisper-api path: one catch-all segment.
    "text": TEXT,
    "segments": [{"words": [{"word": word, "start": start, "end": end} for word, start, end in WORDS]}],
}

LEGACY_LOCAL_WHISPER_RAW = {
    # Shape written by the pre-boundary local whisper CLI path.
    "text": TEXT,
    "language": "en",
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 1.4,
            "text": " Hello there, friend.",
            "words": [{"word": f" {word}", "start": start, "end": end} for word, start, end in WORDS[:5]],
        },
        {
            "id": 1,
            "start": 1.6,
            "end": 2.8,
            "text": " This is a test.",
            "words": [{"word": f" {word}", "start": start, "end": end} for word, start, end in WORDS[5:]],
        },
    ],
}


def make_project(root: Path) -> Path:
    project = root / "project-test"
    for name in ("work", "output", "config", "input"):
        (project / name).mkdir(parents=True, exist_ok=True)
    return project


class FakeProvider:
    """Stands in for any provider: the pipeline only sees the canonical result."""

    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model
        self.requests: list[Any] = []

    def resolve_model(self, params):
        return self.model

    def transcribe(self, request):
        self.requests.append(request)
        return TranscriptionResult(
            transcript=canonical_transcript(self.name, self.model),
            provider_response={"provider": self.name, "model": self.model, "response": {"native": "payload"}},
        )


class TranscriptReaderTests(unittest.TestCase):
    def test_canonical_and_both_legacy_raw_shapes_yield_the_same_words(self) -> None:
        expected = [
            {"word": word, "start": start, "end": end}
            for word, start, end in WORDS
        ]
        canonical = canonical_transcript("openai", "whisper-1").to_dict()

        self.assertEqual(iter_transcript_words(canonical), expected)
        self.assertEqual(iter_transcript_words(LEGACY_WHISPER_API_RAW), expected)
        self.assertEqual(iter_transcript_words(LEGACY_LOCAL_WHISPER_RAW), expected)

    def test_canonical_words_are_not_double_counted(self) -> None:
        # Canonical documents hold the words flat and per segment; reading must not
        # return them twice.
        canonical = canonical_transcript("local-whisper", "small").to_dict()
        self.assertEqual(len(canonical["words"]), len(WORDS))
        self.assertEqual(len(iter_transcript_words(canonical)), len(WORDS))


class CombineStageTests(unittest.TestCase):
    """The container pipeline's combine stage reads words through the same reader."""

    def combine(self, transcript: dict) -> list[dict]:
        params = {"max_char_chunk_per_sentence": 200, "max_char_chunk": 700}
        words = iter_transcript_words(transcript)
        sentences = local_worker.combine_words_to_sentences_local(words, params)
        return local_worker.combine_sentences_to_chunks_local(sentences, params)

    def test_every_provider_and_legacy_shape_combines_to_the_same_chunks(self) -> None:
        from_openai = self.combine(canonical_transcript("openai", "whisper-1").to_dict())
        from_local = self.combine(canonical_transcript("local-whisper", "small").to_dict())
        from_legacy_api = self.combine(LEGACY_WHISPER_API_RAW)
        from_legacy_local = self.combine(LEGACY_LOCAL_WHISPER_RAW)

        self.assertEqual(from_openai, from_local)
        self.assertEqual(from_openai, from_legacy_api)
        self.assertEqual(from_openai, from_legacy_local)
        self.assertEqual([chunk["text"] for chunk in from_openai], ["Hello there, friend. This is a test."])
        for chunk in from_openai:
            self.assertIsInstance(chunk["start_seconds"], float)
            self.assertIsInstance(chunk["end_seconds"], float)

    def test_speaker_labels_split_chunks_the_same_way_for_any_provider(self) -> None:
        transcript = canonical_transcript("openai", "whisper-1").to_dict()
        words = iter_transcript_words(transcript)
        for index, word in enumerate(words):
            word["speaker"] = "SPEAKER_00" if index < 5 else "SPEAKER_01"
        params = {"max_char_chunk_per_sentence": 200, "max_char_chunk": 700}
        chunks = local_worker.combine_sentences_to_chunks_local(
            local_worker.combine_words_to_sentences_local(words, params), params
        )

        self.assertEqual([chunk["speaker"] for chunk in chunks], ["SPEAKER_00", "SPEAKER_01"])


class TranscribeStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project = make_project(Path(self.temp_dir.name))
        self.source = self.project / "work" / "source.mp3"
        self.source.write_bytes(b"fake-audio")

    def run_transcribe(self, params: dict, provider: FakeProvider):
        with patch.object(local_worker, "create_stt_provider", return_value=provider):
            return local_worker.transcribe(self.source, params, self.project, LOGGER)

    def test_openai_workflow_writes_canonical_raw_and_untouched_response(self) -> None:
        provider = FakeProvider("openai", "whisper-1")
        chunks = self.run_transcribe({"whisper_api": True}, provider)

        raw = json.loads((self.project / "work" / "source.raw.json").read_text(encoding="utf-8"))
        validate_transcript(raw)
        self.assertEqual(raw["provider"], "openai")
        self.assertEqual(raw["provider_response_path"], "source.stt-provider-response.json")

        response = json.loads((self.project / "work" / "source.stt-provider-response.json").read_text(encoding="utf-8"))
        self.assertEqual(response["response"], {"native": "payload"})
        # The untouched payload is kept out of the normalized transcript.
        self.assertNotIn("native", json.dumps(raw))

        self.assertTrue(chunks)
        self.assertEqual(
            json.loads((self.project / "work" / "source.combined.json").read_text(encoding="utf-8")),
            chunks,
        )
        self.assertEqual((self.project / "output" / "source.transcript.txt").read_text(encoding="utf-8"), TEXT)

    def test_local_whisper_workflow_produces_the_same_artifacts(self) -> None:
        provider = FakeProvider("local-whisper", "small")
        chunks = self.run_transcribe({"stt_provider": "local-whisper"}, provider)

        raw = json.loads((self.project / "work" / "source.raw.json").read_text(encoding="utf-8"))
        validate_transcript(raw)
        self.assertEqual(raw["provider"], "local-whisper")
        self.assertTrue(chunks)

    def test_legacy_whisper_api_false_project_selects_local_whisper(self) -> None:
        captured: list[str] = []

        def fake_create(name):
            captured.append(name)
            return FakeProvider("local-whisper", "small")

        with patch.object(local_worker, "create_stt_provider", side_effect=fake_create):
            local_worker.transcribe(self.source, {"whisper_api": False}, self.project, LOGGER)

        self.assertEqual(captured, ["local-whisper"])

    def test_speaker_recognition_annotates_canonical_words(self) -> None:
        provider = FakeProvider("openai", "whisper-1")
        turns = [{"speaker": "SPEAKER_00", "start": 0.0, "end": 3.0}]
        with patch.object(local_worker, "diarize_speakers", return_value=turns):
            self.run_transcribe({"speaker_recognition": True, "number_of_speakers": 2}, provider)

        raw = json.loads((self.project / "work" / "source.raw.json").read_text(encoding="utf-8"))
        validate_transcript(raw)
        self.assertTrue(all(word["speaker"] == "SPEAKER_00" for word in raw["words"]))
        self.assertEqual(raw["speaker_diarization"], turns)
        self.assertTrue((self.project / "work" / "source.diarization.json").exists())


class DownstreamStageTests(unittest.TestCase):
    """Translate, improve, and voiceover run unchanged on canonical transcripts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project = make_project(Path(self.temp_dir.name))
        self.source = self.project / "work" / "source.mp3"
        self.source.write_bytes(b"fake-audio")
        self.params = {
            "language": "RU",
            "target_language": "RU",
            "translation_text_key": "dltrans",
            "improved_text_key": "imp",
            "max_char_chunk_per_sentence": 200,
            "max_char_chunk": 700,
            "sleep_time_tts": 0,
        }

    def chunks_for(self, provider_name: str, model: str) -> list[dict]:
        provider = FakeProvider(provider_name, model)
        with patch.object(local_worker, "create_stt_provider", return_value=provider):
            return local_worker.transcribe(self.source, dict(self.params), self.project, LOGGER)

    def translate(self, chunks: list[dict]) -> list[dict]:
        with patch.object(local_worker, "translate_with_openai", side_effect=lambda text, language: f"[{language}] {text}"):
            with patch.object(local_worker.time, "sleep"):
                return local_worker.translate_segments(chunks, "RU", self.params, self.project, LOGGER)

    def improve(self, translated: list[dict]) -> list[dict]:
        improved_payload = {f"chunk_{index:04d}": f"improved {index}" for index in range(len(translated))}

        class FakeCompletions:
            def create(self, **kwargs):
                message = types.SimpleNamespace(content=json.dumps(improved_payload))
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        class FakeOpenAI:
            def __init__(self, api_key=None):
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        openai_stub = types.ModuleType("openai")
        openai_stub.OpenAI = FakeOpenAI
        with patch.dict(sys.modules, {"openai": openai_stub}):
            with patch.dict(os.environ, {"OPENAI_IMPROVE_MODEL": "gpt-5"}, clear=False):
                with patch.object(local_worker.time, "sleep"):
                    return local_worker.improve_segments(translated, self.params, self.project, LOGGER)

    def voiceover(self, improved: list[dict]) -> list[dict]:
        def fake_tts(text, output, params):
            Path(output).write_bytes(b"mp3")

        with patch.object(local_worker, "synthesize_openai_tts", side_effect=fake_tts):
            with patch.object(local_worker.time, "sleep"):
                return local_worker.synthesize_segments(improved, self.params, self.project, LOGGER)

    def test_openai_transcript_flows_through_translate_improve_and_voiceover(self) -> None:
        chunks = self.chunks_for("openai", "whisper-1")
        translated = self.translate(chunks)
        improved = self.improve(translated)
        synthesized = self.voiceover(improved)

        self.assertTrue(all(item["dltrans"].startswith("[RU] ") for item in translated))
        self.assertTrue(all(item["imp"].startswith("improved ") for item in improved))
        self.assertTrue(all(Path(item["tts_path"]).exists() for item in synthesized))
        self.assertTrue((self.project / "work" / "source.translated.json").exists())
        self.assertTrue((self.project / "work" / "source.improved.json").exists())

    def test_local_whisper_transcript_flows_through_the_same_stages(self) -> None:
        chunks = self.chunks_for("local-whisper", "small")
        translated = self.translate(chunks)
        improved = self.improve(translated)
        synthesized = self.voiceover(improved)

        self.assertEqual(len(synthesized), len(chunks))
        self.assertTrue(all(item["imp"] for item in improved))


if __name__ == "__main__":
    unittest.main()
