"""The container transcribe stage, exercised through the provider boundary.

Needs the worker runtime dependencies (python-dotenv, pytz, azure-storage-blob),
the same set ``test_speaker_diarization.py`` requires.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKER_ROOT = Path(__file__).resolve().parent
PROCESSING_DIR = WORKER_ROOT / "processing_container"
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(PROCESSING_DIR))

from stt import CanonicalTranscript, TranscriptionResult, TranscriptSegment, TranscriptWord, validate_transcript


def load_stage(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PROCESSING_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


raw_transcribe = load_stage("pd_010_raw_transcribe", "pd-010-raw-transcribe.py")

WORDS = [("Hello", 0.0, 0.4), ("there", 0.4, 0.9), (".", 0.9, 0.9), ("Bye", 1.2, 1.6), (".", 1.6, 1.6)]
TEXT = "Hello there. Bye."


class FakeProvider:
    def __init__(self, name: str = "openai", model: str = "whisper-1") -> None:
        self.name = name
        self.model = model
        self.requests: list = []

    def resolve_model(self, params):
        return self.model

    def transcribe(self, request):
        self.requests.append(request)
        words = [TranscriptWord(word=word, start=start, end=end) for word, start, end in WORDS]
        transcript = CanonicalTranscript(
            text=TEXT,
            segments=[TranscriptSegment(id=0, start=0.0, end=1.6, text=TEXT, words=words)],
            provider=self.name,
            model=self.model,
            language="en",
            duration=1.6,
        )
        return TranscriptionResult(
            transcript=transcript,
            provider_response={"provider": self.name, "model": self.model, "response": {"native": "payload"}},
        )


class RawTranscribeStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

        # get_params() resolves parameters.json relative to the working directory,
        # the layout worker_poll.py sets up before running the orchestrator.
        params_dir = self.root / "cwd" / "backend" / "processing_container"
        params_dir.mkdir(parents=True)
        shutil.copyfile(PROCESSING_DIR / "parameters.json", params_dir / "parameters.json")

        self.previous_cwd = Path.cwd()
        os.chdir(self.root / "cwd")
        self.addCleanup(lambda: os.chdir(self.previous_cwd))

        # Keep the stage from spawning the real macOS caffeinate helper.
        caffeinate = patch.object(raw_transcribe, "maybe_start_caffeinate", return_value=None)
        caffeinate.start()
        self.addCleanup(caffeinate.stop)

        self.project = self.root / "project"
        self.project.mkdir()
        self.source = self.project / "source.mp3"
        self.source.write_bytes(b"fake-audio")

    def write_params(self, params: dict) -> None:
        (self.project / "source.params.json").write_text(json.dumps(params), encoding="utf-8")

    def run_stage(self, params: dict, provider: FakeProvider, model_size: str = "") -> None:
        self.write_params(params)
        with patch.object(raw_transcribe, "create_stt_provider", return_value=provider):
            raw_transcribe.main(path=str(self.source), model_size=model_size)

    def read_raw(self) -> dict:
        return json.loads((self.project / "source.raw.json").read_text(encoding="utf-8"))

    def test_openai_workflow_writes_canonical_transcript_and_transcript_text(self) -> None:
        provider = FakeProvider()
        self.run_stage({"whisper_api": True, "stages_to_run": "all", "language": "EN"}, provider)

        raw = self.read_raw()
        validate_transcript(raw)
        self.assertEqual(raw["provider"], "openai")
        self.assertEqual(raw["model"], "whisper-1")
        self.assertEqual(raw["provider_response_path"], "source.stt-provider-response.json")
        self.assertEqual((self.project / "source.transcript.txt").read_text(encoding="utf-8"), TEXT)

    def test_provider_response_is_stored_untouched_and_separately(self) -> None:
        self.run_stage({"whisper_api": True, "stages_to_run": "all"}, FakeProvider())

        response = json.loads((self.project / "source.stt-provider-response.json").read_text(encoding="utf-8"))
        self.assertEqual(response["response"], {"native": "payload"})
        self.assertNotIn("native", json.dumps(self.read_raw()))

    def test_legacy_whisper_api_false_project_runs_local_whisper(self) -> None:
        captured: list[str] = []

        def fake_create(name):
            captured.append(name)
            return FakeProvider("local-whisper", "small")

        self.write_params({"whisper_api": False, "stages_to_run": "all"})
        with patch.object(raw_transcribe, "create_stt_provider", side_effect=fake_create):
            raw_transcribe.main(path=str(self.source), model_size="large")

        self.assertEqual(captured, ["local-whisper"])
        self.assertEqual(self.read_raw()["provider"], "local-whisper")

    def test_stt_provider_setting_overrides_the_legacy_flag(self) -> None:
        captured: list[str] = []

        def fake_create(name):
            captured.append(name)
            return FakeProvider(name, "small")

        self.write_params({"stt_provider": "local-whisper", "whisper_api": True, "stages_to_run": "all"})
        with patch.object(raw_transcribe, "create_stt_provider", side_effect=fake_create):
            raw_transcribe.main(path=str(self.source), model_size="")

        self.assertEqual(captured, ["local-whisper"])

    def test_model_argument_and_legacy_chunking_params_reach_the_provider(self) -> None:
        provider = FakeProvider("local-whisper", "large")
        self.run_stage(
            {"whisper_api": False, "whisper_chunk_length_sec": 120, "stages_to_run": "all"},
            provider,
            model_size="large",
        )

        request = provider.requests[0]
        self.assertEqual(request.params["stt_model"], "large")
        self.assertEqual(request.params["whisper_chunk_length_sec"], 120)
        # Defaults from parameters.json are merged in for anything the project omits.
        self.assertEqual(request.params["stt_max_upload_size_mb"], 24)


class CombineStageReaderTests(unittest.TestCase):
    """pd-020 reads canonical transcripts and legacy raw files with one reader."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.combine = load_stage("pd_020_combine", "pd-020-combine.py")

    def test_canonical_and_legacy_transcripts_yield_identical_words(self) -> None:
        expected = [{"word": word, "start": start, "end": end} for word, start, end in WORDS]
        canonical = CanonicalTranscript(
            text=TEXT,
            segments=[
                TranscriptSegment(
                    id=0,
                    start=0.0,
                    end=1.6,
                    text=TEXT,
                    words=[TranscriptWord(word=word, start=start, end=end) for word, start, end in WORDS],
                )
            ],
            provider="openai",
            model="whisper-1",
            language="en",
        ).to_dict()
        legacy = {"text": TEXT, "segments": [{"words": expected}]}

        self.assertEqual(self.combine.get_words_timings_from_raw(canonical), expected)
        self.assertEqual(self.combine.get_words_timings_from_raw(legacy), expected)


if __name__ == "__main__":
    unittest.main()
