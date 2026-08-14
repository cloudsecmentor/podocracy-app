from __future__ import annotations

import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from processing_container import shared_functions
from processing_container.speaker_diarization import assign_speakers_to_words, diarize_speakers


class SpeakerRecognitionTests(unittest.TestCase):
    def test_runs_pyannote_with_requested_speaker_count_and_exclusive_turns(self) -> None:
        calls = {}

        class FakeAnnotation:
            def itertracks(self, yield_label: bool):
                self.yield_label = yield_label
                return iter(
                    [
                        (SimpleNamespace(start=0.1, end=1.2), None, "SPEAKER_00"),
                        (SimpleNamespace(start=1.2, end=2.3), None, "SPEAKER_01"),
                    ]
                )

        class FakePipelineInstance:
            def __call__(self, audio_path: str, num_speakers: int):
                calls["audio_path"] = audio_path
                calls["num_speakers"] = num_speakers
                return SimpleNamespace(exclusive_speaker_diarization=FakeAnnotation())

        class FakePipeline:
            @classmethod
            def from_pretrained(cls, model_source: str, token: str):
                calls["model_source"] = model_source
                calls["token"] = token
                return FakePipelineInstance()

        pyannote_module = types.ModuleType("pyannote")
        audio_module = types.ModuleType("pyannote.audio")
        audio_module.Pipeline = FakePipeline

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "sample.mp3"
            audio_path.touch()
            with (
                patch.dict(os.environ, {"HF_TOKEN": "test-token"}, clear=False),
                patch.dict(
                    sys.modules,
                    {"pyannote": pyannote_module, "pyannote.audio": audio_module},
                ),
            ):
                turns = diarize_speakers(audio_path, 2, logging.getLogger("test"))

        self.assertEqual(calls["num_speakers"], 2)
        self.assertEqual(calls["token"], "test-token")
        self.assertEqual([turn["speaker"] for turn in turns], ["SPEAKER_00", "SPEAKER_01"])

    def test_assigns_covering_or_nearest_speaker_turn_by_word_midpoint(self) -> None:
        turns = [
            {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
            {"start": 3.0, "end": 4.0, "speaker": "SPEAKER_01"},
        ]
        words = [
            {"word": "first", "start": 0.2, "end": 0.8},
            {"word": "gap-left", "start": 1.8, "end": 2.0},
            {"word": "gap-right", "start": 2.0, "end": 2.4},
            {"word": "second", "start": 3.2, "end": 3.8},
        ]

        assigned = assign_speakers_to_words(words, turns)

        self.assertEqual(
            [word["speaker"] for word in assigned],
            ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01", "SPEAKER_01"],
        )

    def test_speaker_change_splits_sentences_and_chunks(self) -> None:
        words = [
            {"word": "Hello", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_00"},
            {"word": "there", "start": 0.5, "end": 0.9, "speaker": "SPEAKER_00"},
            {"word": "General", "start": 1.0, "end": 1.4, "speaker": "SPEAKER_01"},
            {"word": "Kenobi", "start": 1.5, "end": 1.9, "speaker": "SPEAKER_01"},
        ]
        parameters = {
            "max_char_chunk_per_sentence": 200,
            "max_char_chunk": 700,
            "delay_between_words_for_new_sentence_chunk": 2.0,
        }

        with patch.object(
            shared_functions,
            "get_params",
            side_effect=lambda name, **_: parameters[name],
        ):
            sentences = shared_functions.combine_words_to_sentences(words, "source.mp3")
            chunks = shared_functions.combine_sentences_to_chunks(sentences, "source.mp3", "mmss")

        self.assertEqual([sentence["speaker"] for sentence in sentences], ["SPEAKER_00", "SPEAKER_01"])
        self.assertEqual([chunk["speaker"] for chunk in chunks], ["SPEAKER_00", "SPEAKER_01"])
        self.assertEqual([chunk["text"] for chunk in chunks], ["Hello there", "General Kenobi"])


if __name__ == "__main__":
    unittest.main()
