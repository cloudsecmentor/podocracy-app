from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

IMPORT_PROJECTS_DIR = tempfile.TemporaryDirectory()
os.environ["PROJECTS_DIR"] = IMPORT_PROJECTS_DIR.name

import api


def tearDownModule() -> None:
    IMPORT_PROJECTS_DIR.cleanup()


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
                speaker_recognition="true",
                number_of_speakers="2",
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
        self.assertTrue(params["speaker_recognition"])
        self.assertEqual(params["number_of_speakers"], 2)

        with self.assertRaisesRegex(api.HTTPException, "Only draft projects can be started"):
            self.start_project(draft["id"])

    def test_missing_transcript_still_creates_audio_draft(self) -> None:
        with patch.object(
            api,
            "download_bema_episode",
            return_value=("e034.mp3", b"audio", ""),
        ):
            project = api.import_bema_episode(api.ImportBemaEpisodeRequest(episode=34))

        root = api.PROJECTS_DIR / project["id"]
        self.assertEqual(project["status"]["state"], "draft")
        self.assertEqual((root / "input" / "e034.mp3").read_bytes(), b"audio")
        self.assertFalse((root / "input" / "e034.proofread.txt").exists())
        self.assertFalse(project["metadata"]["transcript_uploaded"])
        self.assertEqual(
            project["metadata"]["transcript_warning"],
            "BEMA episode transcript could not be downloaded",
        )
        self.assertIn("without transcript", project["status"]["message"])


class VibeVoiceProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        api.PROJECTS_DIR = Path(self.temp_dir.name)

    def build_payload(self, **overrides):
        kwargs = dict(
            filename="source.mp3",
            language="RU",
            voice="SEBBE",
            stage_preset="voiceover",
            stages_to_run="transcribe+voiceover",
            custom_instructions="",
            tts_api="vibevoice",
            translation_provider="openai",
            elevenlabs_voice_id="",
            vibevoice_model="7B",
            vibevoice_cfg_scale="1.3",
            vibevoice_speed="1.0",
            voiceover_tempo="1.2",
            voiceover_shift="1.5",
            normalize_final_audio="",
            max_preview_size_mb="2",
            use_subtitles_as_is="",
            autogenerate_custom_instructions="",
            detailed_transcription="true",
            speaker_recognition="",
            number_of_speakers="2",
            whisper_chunk_length_sec="300",
            whisper_silence_split="",
            whisper_silence_sec="2",
            max_char_chunk_per_sentence="200",
            max_char_chunk="400",
            improve_max_chunk_chars="12000",
        )
        kwargs.update(overrides)
        return api.build_configured_project_payload(**kwargs)

    def test_parse_tts_api_accepts_vibevoice_and_still_rejects_unknown(self) -> None:
        self.assertEqual(api.parse_tts_api("vibevoice"), "vibevoice")
        self.assertEqual(api.parse_tts_api("VibeVoice"), "vibevoice")
        with self.assertRaisesRegex(api.HTTPException, "openai, elevenlabs, or vibevoice"):
            api.parse_tts_api("bogus")

    def test_voiceover_without_base_url_is_rejected_by_name(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            os.environ.pop("VIBEVOICE_BASE_URL", None)
            with self.assertRaisesRegex(api.HTTPException, "VIBEVOICE_BASE_URL is required"):
                self.build_payload()

    def test_configured_base_url_persists_vibevoice_params(self) -> None:
        env = {"OPENAI_API_KEY": "test-key", "VIBEVOICE_BASE_URL": "http://127.0.0.1:8000/v1"}
        with patch.dict(os.environ, env, clear=False):
            params, metadata = self.build_payload()

        self.assertEqual(params["tts_api"], "vibevoice")
        self.assertEqual(metadata["tts_api"], "vibevoice")
        self.assertEqual(params["voice"], "SEBBE")
        self.assertEqual(params["vibevoice_model"], "7B")
        self.assertEqual(params["vibevoice_cfg_scale"], "1.3")
        self.assertEqual(params["vibevoice_speed"], "1.0")

    def test_empty_vibevoice_fields_are_not_persisted(self) -> None:
        env = {"OPENAI_API_KEY": "test-key", "VIBEVOICE_BASE_URL": "http://127.0.0.1:8000/v1"}
        with patch.dict(os.environ, env, clear=False):
            params, _ = self.build_payload(vibevoice_model="", vibevoice_cfg_scale="", vibevoice_speed="")

        self.assertNotIn("vibevoice_model", params)
        self.assertNotIn("vibevoice_cfg_scale", params)
        self.assertNotIn("vibevoice_speed", params)

    def test_provider_status_reads_env_only_and_makes_no_network_call(self) -> None:
        with patch.object(api.requests, "get") as get_mock, patch.object(api.requests, "post") as post_mock:
            with patch.dict(os.environ, {"VIBEVOICE_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=False):
                self.assertTrue(api.provider_status()["vibevoice"])
            with patch.dict(os.environ, {"VIBEVOICE_BASE_URL": "   "}, clear=False):
                self.assertFalse(api.provider_status()["vibevoice"])
        get_mock.assert_not_called()
        post_mock.assert_not_called()

    def test_voices_proxy_returns_503_not_500_when_server_is_down(self) -> None:
        import requests as requests_module

        with patch.dict(os.environ, {"VIBEVOICE_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=False):
            with patch.object(api.requests, "get", side_effect=requests_module.ConnectionError("refused")):
                with self.assertRaises(api.HTTPException) as caught:
                    api.vibevoice_voices()

        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("not reachable", caught.exception.detail)
        self.assertNotIn("refused", caught.exception.detail)

    def test_voices_proxy_normalizes_payload_and_default(self) -> None:
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"voices": [{"id": "SEBBE"}, "ALICE", {"name": "SEBBE"}]}

        with patch.dict(os.environ, {"VIBEVOICE_BASE_URL": "http://127.0.0.1:8000/v1/"}, clear=False):
            os.environ.pop("VIBEVOICE_TTS_VOICE", None)
            with patch.object(api.requests, "get", return_value=FakeResponse()) as get_mock:
                payload = api.vibevoice_voices()

        get_mock.assert_called_once()
        self.assertEqual(get_mock.call_args[0][0], "http://127.0.0.1:8000/v1/voices")
        self.assertEqual(payload["voices"], ["SEBBE", "ALICE"])
        self.assertEqual(payload["default"], "SEBBE")

    def test_voices_proxy_rejects_non_http_scheme(self) -> None:
        with patch.dict(os.environ, {"VIBEVOICE_BASE_URL": "file:///etc/passwd"}, clear=False):
            with self.assertRaises(api.HTTPException) as caught:
                api.vibevoice_voices()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("http or https", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
