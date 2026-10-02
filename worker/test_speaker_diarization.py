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
from processing_container.speaker_diarization import (
    assign_speakers_to_words,
    diarization_engine,
    diarize_speakers,
    exclusive_turns_from_probabilities,
)


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
                patch.dict(os.environ, {"HF_TOKEN": "test-token", "DIARIZATION_ENGINE": "pyannote"}, clear=False),
                patch.dict(
                    sys.modules,
                    {"pyannote": pyannote_module, "pyannote.audio": audio_module},
                ),
            ):
                turns = diarize_speakers(audio_path, 2, logging.getLogger("test"))

        self.assertEqual(calls["num_speakers"], 2)
        self.assertEqual(calls["token"], "test-token")
        self.assertEqual([turn["speaker"] for turn in turns], ["SPEAKER_00", "SPEAKER_01"])

    def test_unknown_diarization_engine_is_rejected(self) -> None:
        with patch.dict(os.environ, {"DIARIZATION_ENGINE": "whisperx"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "pyannote, nemotron"):
                diarization_engine()

    def test_nemotron_overlap_goes_to_the_most_likely_speaker(self) -> None:
        import numpy as np

        probabilities = np.zeros((10, 8), dtype=np.float32)
        probabilities[0:6, 0] = 0.9
        probabilities[4:10, 1] = 0.95

        turns = exclusive_turns_from_probabilities(probabilities, 0.1, 2)

        self.assertEqual(
            turns,
            [
                {"start": 0.0, "end": 0.4, "speaker": "SPEAKER_00"},
                {"start": 0.4, "end": 1.0, "speaker": "SPEAKER_01"},
            ],
        )

    def test_nemotron_keeps_only_the_requested_speakers_by_talk_time(self) -> None:
        import numpy as np

        probabilities = np.zeros((20, 8), dtype=np.float32)
        probabilities[0:4, 0] = 0.9
        probabilities[0:4, 1] = 0.3
        probabilities[4:12, 1] = 0.9
        probabilities[12:20, 2] = 0.9

        turns = exclusive_turns_from_probabilities(probabilities, 0.1, 2)

        # Channel 0 talks least, so its speech is handed to the likelier kept channel.
        self.assertEqual(
            turns,
            [
                {"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00"},
                {"start": 1.2, "end": 2.0, "speaker": "SPEAKER_01"},
            ],
        )

    def test_nemotron_absorbs_short_flicker_and_keeps_silence_gaps(self) -> None:
        import numpy as np

        probabilities = np.zeros((30, 8), dtype=np.float32)
        probabilities[0:10, 0] = 0.9
        probabilities[10:12, 1] = 0.9
        probabilities[12:20, 0] = 0.9
        probabilities[25:30, 1] = 0.9

        turns = exclusive_turns_from_probabilities(probabilities, 0.1, 2)

        self.assertEqual(
            turns,
            [
                {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
                {"start": 2.5, "end": 3.0, "speaker": "SPEAKER_01"},
            ],
        )

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
