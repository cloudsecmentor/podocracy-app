from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api


class BemaDraftWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        api.PROJECTS_DIR = Path(self.temp_dir.name)

    def import_episode(self) -> dict:
        with patch.object(
            api,
            "download_bema_episode",
            return_value=("e034.mp3", b"audio", "Imported transcript"),
        ):
            return api.import_bema_episode(api.ImportBemaEpisodeRequest(episode=34))

    def start_project(self, project_id: str) -> dict:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            return api.start_draft_project(
                project_id=project_id,
                subtitle_file=None,
                custom_recordings=None,
                language="RU",
                voice="coral",
                stage_preset="voiceover",
                stages_to_run="translate+improve",
                custom_instructions="Keep names unchanged.",
                tts_api="openai",
                translation_provider="openai",
                elevenlabs_voice_id="",
                voiceover_tempo="1.1",
                voiceover_shift="2.5",
                normalize_final_audio="true",
                max_preview_size_mb="2",
                use_subtitles_as_is="",
                autogenerate_custom_instructions="",
                detailed_transcription="true",
                whisper_chunk_length_sec="300",
                whisper_silence_split="",
                whisper_silence_sec="2",
                max_char_chunk_per_sentence="200",
                max_char_chunk="400",
                improve_max_chunk_chars="12000",
            )

    def test_import_creates_draft_without_runnable_parameters(self) -> None:
        project = self.import_episode()
        root = api.PROJECTS_DIR / project["id"]

        self.assertEqual(project["status"]["state"], "draft")
        self.assertEqual((root / "input" / "e034.mp3").read_bytes(), b"audio")
        self.assertEqual(
            (root / "input" / "e034.proofread.txt").read_text(encoding="utf-8"),
            "Imported transcript",
        )
        self.assertFalse((root / "config" / "params.json").exists())
        self.assertFalse((root / "input" / "e034.params.json").exists())

    def test_start_applies_configuration_and_queues_draft(self) -> None:
        draft = self.import_episode()
        project = self.start_project(draft["id"])
        root = api.PROJECTS_DIR / project["id"]
        params = json.loads((root / "config" / "params.json").read_text(encoding="utf-8"))

        self.assertEqual(project["status"]["state"], "queued")
        self.assertEqual(params["language"], "RU")
        self.assertEqual(params["voice"], "coral")
        self.assertEqual(params["stages_to_run"], "translate+improve")
        self.assertEqual(params["custom_instructions"], "Keep names unchanged.")
        self.assertEqual(params["bema_episode"], 34)

        with self.assertRaisesRegex(api.HTTPException, "Only draft projects can be started"):
            self.start_project(draft["id"])

    def test_requested_missing_transcript_does_not_create_project(self) -> None:
        with patch.object(
            api,
            "download_bema_episode",
            return_value=("e034.mp3", b"audio", ""),
        ):
            with self.assertRaisesRegex(api.HTTPException, "transcript could not be downloaded"):
                api.import_bema_episode(api.ImportBemaEpisodeRequest(episode=34))

        self.assertEqual(list(api.PROJECTS_DIR.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
