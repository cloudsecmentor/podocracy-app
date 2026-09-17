from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stt import (
    STT_PROVIDER_LOCAL_WHISPER,
    STT_PROVIDER_OPENAI,
    SUPPORTED_STT_PROVIDERS,
    SttCredentialsError,
    TranscriptionRequest,
    TranscriptSchemaError,
    UnknownSttProviderError,
    create_stt_provider,
    iter_transcript_words,
    legacy_whisper_api_value,
    normalize_stt_provider,
    replace_transcript_words,
    resolve_stt_provider_name,
    validate_transcript,
)
from stt.local_whisper import LocalWhisperSttProvider
from stt.openai_whisper import OpenAiSttProvider


# --- fakes -------------------------------------------------------------------

class FakeAudioSegment:
    """Minimal stand-in for the pydub object the OpenAI provider slices and exports."""

    duration_seconds = 6.0

    def __init__(self, length_ms: int = 6000) -> None:
        self.length_ms = length_ms

    def __len__(self) -> int:
        return self.length_ms

    def __getitem__(self, item):
        return self

    def export(self, path, format: str = "mp3", bitrate: str | None = None):
        Path(path).write_bytes(b"fake-audio")
        return path


def openai_verbose_json(text: str, words: list[tuple[str, float, float]]) -> dict:
    return {
        "task": "transcribe",
        "language": "english",
        "duration": 6.0,
        "text": text,
        "words": [{"word": word, "start": start, "end": end} for word, start, end in words],
        "segments": [{"id": 0, "start": 0.0, "end": 6.0, "text": text}],
    }


class FakeTranscriptions:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


class FakeOpenAiClient:
    def __init__(self, responses: list[dict]) -> None:
        self.audio = types.SimpleNamespace(transcriptions=FakeTranscriptions(responses))


WORDS = [
    ("Hello", 0.0, 0.4),
    ("there", 0.4, 0.9),
    ("friend", 0.9, 1.4),
    ("this", 1.6, 1.9),
    ("is", 1.9, 2.1),
    ("a", 2.1, 2.2),
    ("test", 2.2, 2.8),
]
TEXT = "Hello there, friend. This is a test."


def openai_result(params: dict | None = None, project: Path | None = None):
    provider = OpenAiSttProvider()
    client = FakeOpenAiClient([openai_verbose_json(TEXT, WORDS)])
    pydub_stub = types.ModuleType("pydub")
    pydub_stub.AudioSegment = types.SimpleNamespace(from_file=lambda path: FakeAudioSegment())
    with patch.dict(sys.modules, {"pydub": pydub_stub}):
        with patch.object(OpenAiSttProvider, "build_client", return_value=client):
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                with tempfile.TemporaryDirectory() as temp_dir:
                    audio_path = Path(temp_dir) / "source.mp3"
                    audio_path.write_bytes(b"fake-audio")
                    result = provider.transcribe(
                        TranscriptionRequest(
                            audio_path=audio_path,
                            params=params or {},
                            work_dir=project or Path(temp_dir),
                            logger=logging.getLogger("test"),
                        )
                    )
    return result, client


LOCAL_WHISPER_JSON = {
    "text": TEXT,
    "language": "en",
    "segments": [
        {
            "id": 0,
            "seek": 0,
            "start": 0.0,
            "end": 1.4,
            "text": " Hello there, friend.",
            "words": [
                {"word": " Hello", "start": 0.0, "end": 0.4, "probability": 0.9},
                {"word": " there,", "start": 0.4, "end": 0.9, "probability": 0.9},
                {"word": " friend.", "start": 0.9, "end": 1.4, "probability": 0.9},
            ],
        },
        {
            "id": 1,
            "seek": 0,
            "start": 1.6,
            "end": 2.8,
            "text": " This is a test.",
            "words": [
                {"word": " This", "start": 1.6, "end": 1.9, "probability": 0.9},
                {"word": " is", "start": 1.9, "end": 2.1, "probability": 0.9},
                {"word": " a", "start": 2.1, "end": 2.2, "probability": 0.9},
                {"word": " test.", "start": 2.2, "end": 2.8, "probability": 0.9},
            ],
        },
    ],
}


def local_whisper_result(params: dict | None = None):
    provider = LocalWhisperSttProvider()
    with patch.object(LocalWhisperSttProvider, "run_whisper", return_value=LOCAL_WHISPER_JSON):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "source.mp3"
            audio_path.write_bytes(b"fake-audio")
            return provider.transcribe(
                TranscriptionRequest(
                    audio_path=audio_path,
                    params=params or {},
                    logger=logging.getLogger("test"),
                )
            )


# --- provider selection ------------------------------------------------------

class ProviderSelectionTests(unittest.TestCase):
    def test_supported_providers_are_openai_and_local_whisper(self) -> None:
        self.assertEqual(set(SUPPORTED_STT_PROVIDERS), {"openai", "local-whisper"})

    def test_legacy_whisper_api_true_selects_openai(self) -> None:
        self.assertEqual(resolve_stt_provider_name({"whisper_api": True}), STT_PROVIDER_OPENAI)
        self.assertEqual(resolve_stt_provider_name({"whisper_api": "true"}), STT_PROVIDER_OPENAI)

    def test_legacy_whisper_api_false_selects_local_whisper(self) -> None:
        self.assertEqual(resolve_stt_provider_name({"whisper_api": False}), STT_PROVIDER_LOCAL_WHISPER)
        self.assertEqual(resolve_stt_provider_name({"whisper_api": "false"}), STT_PROVIDER_LOCAL_WHISPER)

    def test_stt_provider_wins_over_legacy_flag(self) -> None:
        params = {"stt_provider": "local-whisper", "whisper_api": True}
        self.assertEqual(resolve_stt_provider_name(params), STT_PROVIDER_LOCAL_WHISPER)

    def test_project_setting_wins_over_deployment_default(self) -> None:
        defaults = {"stt_provider": {"value": "local-whisper"}}
        self.assertEqual(resolve_stt_provider_name({"whisper_api": True}, defaults), STT_PROVIDER_OPENAI)
        self.assertEqual(resolve_stt_provider_name({}, defaults), STT_PROVIDER_LOCAL_WHISPER)

    def test_empty_params_default_to_openai(self) -> None:
        self.assertEqual(resolve_stt_provider_name({}), STT_PROVIDER_OPENAI)
        self.assertEqual(resolve_stt_provider_name(None), STT_PROVIDER_OPENAI)

    def test_aliases_are_normalized_and_unknown_names_rejected(self) -> None:
        self.assertEqual(normalize_stt_provider("OpenAI"), STT_PROVIDER_OPENAI)
        self.assertEqual(normalize_stt_provider("local_whisper"), STT_PROVIDER_LOCAL_WHISPER)
        with self.assertRaisesRegex(UnknownSttProviderError, "supported providers"):
            normalize_stt_provider("google")

    def test_legacy_flag_is_still_derivable_for_older_workers(self) -> None:
        self.assertTrue(legacy_whisper_api_value("openai"))
        self.assertFalse(legacy_whisper_api_value("local-whisper"))

    def test_openai_provider_requires_a_key_and_local_does_not(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            with self.assertRaisesRegex(SttCredentialsError, "OPENAI_API_KEY"):
                create_stt_provider("openai").ensure_credentials()
            create_stt_provider("local-whisper").ensure_credentials()


# --- canonical schema --------------------------------------------------------

class CanonicalSchemaTests(unittest.TestCase):
    def test_openai_provider_produces_canonical_transcript(self) -> None:
        result, _ = openai_result()
        document = validate_transcript(result.transcript.to_dict())

        self.assertEqual(document["schema_version"], "podocracy-transcript-v1")
        self.assertEqual(document["provider"], "openai")
        self.assertEqual(document["model"], "whisper-1")
        self.assertEqual(document["language"], "english")
        self.assertEqual(document["text"], TEXT)
        self.assertTrue(document["words"])
        self.assertTrue(all(isinstance(word["start"], float) for word in document["words"]))
        self.assertTrue(all(isinstance(word["end"], float) for word in document["words"]))

    def test_local_whisper_provider_produces_canonical_transcript(self) -> None:
        document = validate_transcript(local_whisper_result().transcript.to_dict())

        self.assertEqual(document["schema_version"], "podocracy-transcript-v1")
        self.assertEqual(document["provider"], "local-whisper")
        self.assertEqual(document["model"], "small")
        self.assertEqual(document["language"], "en")
        self.assertEqual([word["word"] for word in document["words"]][:3], ["Hello", "there,", "friend."])
        self.assertEqual(len(document["segments"]), 2)

    def test_both_providers_agree_on_the_document_shape(self) -> None:
        openai_document = openai_result()[0].transcript.to_dict()
        local_document = local_whisper_result().transcript.to_dict()

        self.assertEqual(set(openai_document), set(local_document))
        for document in (openai_document, local_document):
            for word in document["words"]:
                self.assertEqual(set(word) - {"speaker"}, {"word", "start", "end"})
            for segment in document["segments"]:
                self.assertEqual(set(segment) - {"speaker"}, {"id", "start", "end", "text", "words"})

    def test_transcripts_carry_provider_model_and_language_provenance(self) -> None:
        for document in (openai_result()[0].transcript.to_dict(), local_whisper_result().transcript.to_dict()):
            self.assertIn(document["provider"], SUPPORTED_STT_PROVIDERS)
            self.assertTrue(document["model"])
            self.assertTrue(document["language"])

    def test_speaker_labels_are_optional_and_survive_validation(self) -> None:
        document = local_whisper_result().transcript.to_dict()
        words = iter_transcript_words(document)
        for word in words:
            word["speaker"] = "SPEAKER_00"
        replace_transcript_words(document, words)

        validate_transcript(document)
        self.assertTrue(all(word["speaker"] == "SPEAKER_00" for word in document["words"]))
        self.assertTrue(
            all(word["speaker"] == "SPEAKER_00" for segment in document["segments"] for word in segment["words"])
        )

    def test_validation_rejects_non_numeric_word_timings(self) -> None:
        document = local_whisper_result().transcript.to_dict()
        document["words"][0]["start"] = "start"
        with self.assertRaisesRegex(TranscriptSchemaError, "must be a number"):
            validate_transcript(document)

    def test_validation_rejects_foreign_schema_versions(self) -> None:
        document = local_whisper_result().transcript.to_dict()
        document["schema_version"] = "whisper-raw-v0"
        with self.assertRaisesRegex(TranscriptSchemaError, "Unsupported transcript schema_version"):
            validate_transcript(document)


# --- untouched provider payloads --------------------------------------------

class ProviderResponseTests(unittest.TestCase):
    def test_openai_response_is_preserved_verbatim_outside_the_transcript(self) -> None:
        result, _ = openai_result()
        response = result.provider_response

        self.assertEqual(response["provider"], "openai")
        self.assertEqual(response["model"], "whisper-1")
        self.assertEqual(response["response"]["chunks"][0]["response"], openai_verbose_json(TEXT, WORDS))
        # The normalized transcript never carries the provider payload.
        self.assertNotIn("chunks", result.transcript.to_dict())

    def test_local_whisper_response_is_preserved_verbatim(self) -> None:
        result = local_whisper_result()
        self.assertEqual(result.provider_response["response"], LOCAL_WHISPER_JSON)
        self.assertEqual(result.provider_response["provider"], "local-whisper")

    def test_provider_responses_are_json_serializable(self) -> None:
        for result in (openai_result()[0], local_whisper_result()):
            json.loads(json.dumps(result.provider_response))


# --- provider behaviour ------------------------------------------------------

class OpenAiProviderTests(unittest.TestCase):
    def test_word_timestamps_are_requested_and_punctuation_is_restored(self) -> None:
        result, client = openai_result()
        call = client.audio.transcriptions.calls[0]

        self.assertEqual(call["timestamp_granularities"], ["word"])
        self.assertEqual(call["response_format"], "verbose_json")
        # Punctuation only exists in the text, so it is re-timed as its own token,
        # which is the shape combine_words_to_sentences splits sentences on.
        self.assertEqual(
            [word.word for word in result.transcript.words],
            ["Hello", "there", ",", "friend", ".", "This", "is", "a", "test", "."],
        )

    def test_long_audio_is_chunked_and_word_times_are_offset_per_chunk(self) -> None:
        # A file over the upload limit is split; each chunk's timings are shifted
        # by the chunk offset so the transcript stays on one timeline.
        params = {"stt_max_upload_size_mb": 0.000001, "stt_chunk_length_sec": 3}
        result, client = openai_result(params=params)
        words = result.transcript.words

        self.assertEqual(len(client.audio.transcriptions.calls), 2)
        self.assertEqual(len(words), 20)
        self.assertEqual(words[0].start, 0.0)
        self.assertEqual(words[10].start, 3.0)
        self.assertEqual([word.start for word in words[10:]], [word.start + 3.0 for word in words[:10]])

    def test_legacy_whisper_chunking_params_still_apply(self) -> None:
        params = {"whisper_api_max_size_mp3": 0.000001, "whisper_chunk_length_sec": 3}
        result, client = openai_result(params=params)

        self.assertEqual(len(client.audio.transcriptions.calls), 2)
        self.assertEqual(len(result.transcript.words), 20)

    def test_small_audio_is_sent_as_a_single_request(self) -> None:
        _, client = openai_result(params={"stt_chunk_length_sec": 3})
        self.assertEqual(len(client.audio.transcriptions.calls), 1)

    def test_model_comes_from_params_then_environment(self) -> None:
        provider = OpenAiSttProvider()
        with patch.dict(os.environ, {"OPENAI_TRANSCRIBE_MODEL": "gpt-4o-transcribe"}, clear=False):
            self.assertEqual(provider.resolve_model({}), "gpt-4o-transcribe")
            self.assertEqual(provider.resolve_model({"stt_model": "whisper-1"}), "whisper-1")

    def test_missing_key_fails_before_any_api_call(self) -> None:
        provider = OpenAiSttProvider()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            with patch.object(OpenAiSttProvider, "build_client") as build_client:
                with self.assertRaisesRegex(SttCredentialsError, "OPENAI_API_KEY"):
                    provider.transcribe(TranscriptionRequest(audio_path=Path("missing.mp3")))
        build_client.assert_not_called()


class LocalWhisperProviderTests(unittest.TestCase):
    def test_model_falls_back_from_stt_model_to_local_default(self) -> None:
        provider = LocalWhisperSttProvider()
        self.assertEqual(provider.resolve_model({"stt_model": "large"}), "large")
        self.assertEqual(provider.resolve_model({"stt_local_model": "medium"}), "medium")
        self.assertEqual(provider.resolve_model({"whisper_default_model_local": "base"}), "base")
        self.assertEqual(provider.resolve_model({}), "small")

    def test_legacy_whisper_model_param_still_selects_the_model(self) -> None:
        self.assertEqual(LocalWhisperSttProvider().resolve_model({"whisper_model": "medium.en"}), "medium.en")

    def test_runs_the_whisper_cli_with_word_timestamps(self) -> None:
        provider = LocalWhisperSttProvider()
        captured: dict = {}

        def fake_run(command, check, text, stderr=None):
            captured["command"] = command
            output_dir = Path(command[command.index("--output_dir") + 1])
            (output_dir / "source.json").write_text(json.dumps(LOCAL_WHISPER_JSON), encoding="utf-8")
            return None

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "source.mp3"
            audio_path.write_bytes(b"fake-audio")
            with patch("stt.local_whisper.whisper_is_installed", return_value=True):
                with patch("stt.local_whisper.subprocess.run", side_effect=fake_run):
                    result = provider.transcribe(
                        TranscriptionRequest(
                            audio_path=audio_path,
                            params={"stt_model": "large"},
                            logger=logging.getLogger("test"),
                        )
                    )

        self.assertIn("--word_timestamps", captured["command"])
        self.assertEqual(captured["command"][captured["command"].index("--model") + 1], "large")
        self.assertEqual(result.transcript.model, "large")
        validate_transcript(result.transcript.to_dict())


if __name__ == "__main__":
    unittest.main()
