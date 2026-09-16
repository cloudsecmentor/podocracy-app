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
                stt_provider="openai",
                stt_chunk_length_sec="300",
                stt_silence_split="",
                stt_silence_sec="2",
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
            stt_provider="openai",
            stt_chunk_length_sec="300",
            stt_silence_split="",
            stt_silence_sec="2",
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


class SttProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        api.PROJECTS_DIR = Path(self.temp_dir.name)

    def build_payload(self, **overrides):
        kwargs = dict(
            filename="source.mp3",
            language="RU",
            voice="alloy",
            stage_preset="voiceover",
            stages_to_run="transcribe+translate+improve+voiceover",
            custom_instructions="",
            tts_api="openai",
            translation_provider="openai",
            stt_provider="openai",
            elevenlabs_voice_id="",
            voiceover_tempo="1.2",
            voiceover_shift="1.5",
            normalize_final_audio="",
            max_preview_size_mb="2",
            use_subtitles_as_is="",
            autogenerate_custom_instructions="",
            detailed_transcription="true",
            speaker_recognition="",
            number_of_speakers="2",
            stt_chunk_length_sec="300",
            stt_silence_split="",
            stt_silence_sec="2",
            max_char_chunk_per_sentence="200",
            max_char_chunk="400",
            improve_max_chunk_chars="12000",
        )
        kwargs.update(overrides)
        return api.build_configured_project_payload(**kwargs)

    def test_unpassed_provider_field_falls_back_to_the_default(self) -> None:
        # Called directly (tests, scripts), an omitted Form field is the Form object.
        self.assertEqual(api.parse_stt_provider(api.Form(api.DEFAULT_STT_PROVIDER)), "openai")

    def test_parse_stt_provider_accepts_supported_names_and_legacy_spellings(self) -> None:
        self.assertEqual(api.parse_stt_provider("openai"), "openai")
        self.assertEqual(api.parse_stt_provider("Local-Whisper"), "local-whisper")
        self.assertEqual(api.parse_stt_provider("whisper-api"), "openai")
        self.assertEqual(api.parse_stt_provider(""), "openai")
        with self.assertRaisesRegex(api.HTTPException, "stt_provider must be one of"):
            api.parse_stt_provider("google")

    def test_provider_is_persisted_with_a_legacy_mirror(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            params, metadata = self.build_payload()
        self.assertEqual(params["stt_provider"], "openai")
        self.assertTrue(params["whisper_api"])
        self.assertEqual(metadata["stt_provider"], "openai")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            params, metadata = self.build_payload(stt_provider="local-whisper")
        self.assertEqual(params["stt_provider"], "local-whisper")
        self.assertFalse(params["whisper_api"])
        self.assertEqual(metadata["stt_provider"], "local-whisper")

    def test_chunking_params_are_stored_under_generalized_names(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            params, _ = self.build_payload(stt_chunk_length_sec="120", stt_silence_split="true", stt_silence_sec="3")
        self.assertEqual(params["stt_chunk_length_sec"], 120)
        self.assertTrue(params["stt_silence_split"])
        self.assertEqual(params["stt_silence_sec"], 3.0)

    def test_local_whisper_transcribe_does_not_require_an_openai_key(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            params, _ = self.build_payload(
                stt_provider="local-whisper",
                stages_to_run="transcribe",
            )
        self.assertEqual(params["stt_provider"], "local-whisper")

    def test_openai_key_is_still_required_for_stages_that_use_openai(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaisesRegex(api.HTTPException, "OPENAI_API_KEY is required for these stages: transcribe"):
                self.build_payload(stages_to_run="transcribe")
            with self.assertRaisesRegex(api.HTTPException, "OPENAI_API_KEY is required for these stages: translate"):
                self.build_payload(stt_provider="local-whisper", stages_to_run="translate")
            with self.assertRaisesRegex(api.HTTPException, "OPENAI_API_KEY is required for these stages: voiceover"):
                self.build_payload(stt_provider="local-whisper", stages_to_run="voiceover", tts_api="openai")

    def test_openai_stages_lists_only_the_stages_that_call_openai(self) -> None:
        self.assertEqual(
            api.openai_stages(
                "transcribe+translate+improve+voiceover",
                stt_provider="local-whisper",
                translation_provider="deepl",
                tts_api="elevenlabs",
            ),
            ["improve"],
        )
        self.assertEqual(
            api.openai_stages(
                "transcribe+translate+voiceover",
                stt_provider="openai",
                translation_provider="openai",
                tts_api="openai",
            ),
            ["transcribe", "translate", "voiceover"],
        )

    def test_local_whisper_is_reported_as_an_available_provider(self) -> None:
        self.assertTrue(api.provider_status()["local-whisper"])


class LegacyProjectCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        api.PROJECTS_DIR = Path(self.temp_dir.name)

    def test_pre_rename_whisper_form_fields_are_still_accepted(self) -> None:
        root = api.PROJECTS_DIR / "project-legacy"
        (root / "input").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        (root / "input" / "source.mp3").write_bytes(b"audio")
        api.write_json(root / "metadata.json", {"source_path": "input/source.mp3"})
        api.write_json(root / "status.json", {"state": "draft"})

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            api.start_draft_project(
                project_id="project-legacy",
                subtitle_file=None,
                custom_recordings=None,
                language="RU",
                voice="alloy",
                stage_preset="voiceover",
                stages_to_run="transcribe",
                custom_instructions="",
                tts_api="openai",
                translation_provider="openai",
                stt_provider="openai",
                elevenlabs_voice_id="",
                voiceover_tempo="1.2",
                voiceover_shift="1.5",
                normalize_final_audio="",
                max_preview_size_mb="2",
                use_subtitles_as_is="",
                autogenerate_custom_instructions="",
                detailed_transcription="true",
                speaker_recognition="",
                number_of_speakers="2",
                stt_chunk_length_sec="",
                stt_silence_split="",
                stt_silence_sec="",
                max_char_chunk_per_sentence="200",
                max_char_chunk="400",
                improve_max_chunk_chars="12000",
                whisper_chunk_length_sec="150",
                whisper_silence_split="true",
                whisper_silence_sec="4",
            )

        params = json.loads((root / "config" / "params.json").read_text(encoding="utf-8"))
        self.assertEqual(params["stt_chunk_length_sec"], 150)
        self.assertTrue(params["stt_silence_split"])
        self.assertEqual(params["stt_silence_sec"], 4.0)


if __name__ == "__main__":
    unittest.main()


class SegmentEndpointTests(unittest.TestCase):
    """Per-chunk voiceover audio: listing, recording upload, delete, requeue."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        api.PROJECTS_DIR = Path(self.temp_dir.name)
        self.project_id = "project-test"
        self.root = api.PROJECTS_DIR / self.project_id
        (self.root / "input").mkdir(parents=True)
        (self.root / "config").mkdir(parents=True)
        (self.root / "input" / "e144.mp3").write_bytes(b"audio")
        self.improved = self.root / "input" / "e144.improved.json"
        self.write_transcript(
            [
                {"start": "0000", "end": "0010", "text": "one", "dltrans": "uno", "imp": "first"},
                {"start": "0010", "text": "two", "dltrans": "dos", "imp": "second"},
            ]
        )
        api.write_json(self.root / "config" / "params.json", {"tts_api": "openai", "voice": "alloy"})
        api.write_json(self.root / "metadata.json", {"source_path": "input/e144.mp3"})
        self.set_state("completed")

    def write_transcript(self, chunks: list[dict]) -> None:
        self.improved.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")

    def read_transcript(self) -> list[dict]:
        return json.loads(self.improved.read_text(encoding="utf-8"))

    def set_state(self, state: str, age_seconds: float = 0.0) -> None:
        """A live run heartbeats; `age_seconds` ages it into an orphan."""
        from datetime import datetime, timedelta, timezone

        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        api.write_json(
            self.root / "status.json",
            {
                "project_id": self.project_id,
                "state": state,
                "stage": state,
                "updated_at": stamp,
                "heartbeat": stamp,
            },
        )

    def upload(self, chunk_id: str, filename: str = "c000.webm", payload: bytes = b"recorded-audio"):
        import io

        from fastapi import UploadFile

        return api.upload_segment_audio(
            project_id=self.project_id,
            chunk_id=chunk_id,
            file=UploadFile(file=io.BytesIO(payload), filename=filename),
        )

    def params(self) -> dict:
        return json.loads((self.root / "config" / "params.json").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- listing

    def test_listing_assigns_and_persists_stable_chunk_ids(self) -> None:
        payload = api.list_segments(self.project_id)

        self.assertEqual([item["chunk_id"] for item in payload["segments"]], ["c000", "c001"])
        self.assertEqual([item["chunk_id"] for item in self.read_transcript()], ["c000", "c001"])
        self.assertTrue(all(item["status"] == "missing" for item in payload["segments"]))
        self.assertFalse(payload["busy"])

    def test_listing_reports_a_chunk_without_text_as_skipped(self) -> None:
        self.write_transcript([{"start": "0000", "text": "one", "imp": ""}])
        payload = api.list_segments(self.project_id)
        self.assertEqual(payload["segments"][0]["status"], "skipped")

    def test_listing_resolves_the_file_the_worker_reads(self) -> None:
        # A stale work/source.improved.json must not win over the source-stem
        # file, or the editor and the pipeline edit two different transcripts.
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        (self.root / "work" / "source.improved.json").write_text("[]", encoding="utf-8")
        self.assertEqual(api.list_segments(self.project_id)["filename"], "e144.improved.json")

    # ---------------------------------------------------------------- upload

    def test_upload_parks_the_recording_for_the_worker(self) -> None:
        api.list_segments(self.project_id)
        result = self.upload("c000")

        self.assertEqual(result["status"], "pending_ingest")
        raw = self.root / "work" / "segments" / "raw" / "c000.webm"
        self.assertEqual(raw.read_bytes(), b"recorded-audio")
        entry = api.seg.load_index(self.root)["segments"]["c000"]
        self.assertEqual(entry["source"], "recording")
        self.assertEqual(entry["text_hash"], api.seg.text_hash("first"))

    def test_uploaded_recording_is_playable_before_the_worker_converts_it(self) -> None:
        api.list_segments(self.project_id)
        self.upload("c000")
        response = api.get_segment_audio(self.project_id, "c000")
        self.assertEqual(response.media_type, "audio/webm")

    def test_re_recording_replaces_the_previous_take(self) -> None:
        api.list_segments(self.project_id)
        self.upload("c000", filename="c000.webm")
        self.upload("c000", filename="c000.ogg", payload=b"second-take")

        raw_dir = self.root / "work" / "segments" / "raw"
        self.assertEqual([item.name for item in raw_dir.glob("c000.*")], ["c000.ogg"])
        self.assertEqual((raw_dir / "c000.ogg").read_bytes(), b"second-take")

    def test_upload_replaces_generated_audio_for_that_chunk(self) -> None:
        api.list_segments(self.project_id)
        canonical = self.root / "work" / "segments" / "c000.ogg"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"generated")

        self.upload("c000")

        self.assertFalse(canonical.exists())
        self.assertEqual(api.seg.load_index(self.root)["segments"]["c000"]["source"], "recording")

    def test_upload_rejects_unsupported_types_empty_bodies_and_oversized_files(self) -> None:
        api.list_segments(self.project_id)
        with self.assertRaisesRegex(api.HTTPException, "Unsupported audio type"):
            self.upload("c000", filename="c000.exe")
        with self.assertRaisesRegex(api.HTTPException, "empty"):
            self.upload("c000", payload=b"")
        with patch.object(api, "SEGMENT_UPLOAD_MAX_BYTES", 4):
            with self.assertRaisesRegex(api.HTTPException, "limit"):
                self.upload("c000", payload=b"much too long")

    def test_upload_rejects_an_unknown_chunk(self) -> None:
        api.list_segments(self.project_id)
        with self.assertRaisesRegex(api.HTTPException, "not in the transcript"):
            self.upload("c099")

    def test_invalid_chunk_ids_are_rejected_before_touching_the_filesystem(self) -> None:
        for chunk_id in ("../../etc", "c1", "source"):
            with self.assertRaisesRegex(api.HTTPException, "Invalid chunk id"):
                api.delete_segment_audio(self.project_id, chunk_id)

    # ---------------------------------------------------------------- delete

    def test_delete_removes_every_file_and_the_index_entry(self) -> None:
        api.list_segments(self.project_id)
        self.upload("c000")
        canonical = self.root / "work" / "segments" / "c000.ogg"
        canonical.write_bytes(b"generated")

        api.delete_segment_audio(self.project_id, "c000")

        self.assertFalse(canonical.exists())
        self.assertFalse((self.root / "work" / "segments" / "raw" / "c000.webm").exists())
        self.assertNotIn("c000", api.seg.load_index(self.root)["segments"])
        self.assertEqual(api.list_segments(self.project_id)["segments"][0]["status"], "missing")

    def test_get_audio_is_404_when_the_chunk_has_none(self) -> None:
        api.list_segments(self.project_id)
        with self.assertRaisesRegex(api.HTTPException, "No audio"):
            api.get_segment_audio(self.project_id, "c000")

    # ---------------------------------------------------------------- queueing

    def test_regenerate_scopes_the_job_to_one_chunk(self) -> None:
        api.list_segments(self.project_id)
        project = api.regenerate_segment(self.project_id, "c001")

        self.assertEqual(project["status"]["state"], "queued")
        self.assertEqual(project["status"]["job_kind"], "tts-chunk")
        self.assertEqual(self.params()["stages_to_run"], "tts")
        self.assertEqual(self.params()["voiceover_chunks"], ["c001"])

    def test_regenerate_refuses_a_chunk_with_no_text(self) -> None:
        self.write_transcript([{"start": "0000", "text": "one", "imp": "", "dltrans": ""}])
        api.list_segments(self.project_id)
        with self.assertRaisesRegex(api.HTTPException, "no text"):
            api.regenerate_segment(self.project_id, "c000")

    def test_synthesize_and_build_request_only_their_half(self) -> None:
        self.assertEqual(api.synthesize_missing_segments(self.project_id)["status"]["job_kind"], "tts")
        self.assertEqual(self.params()["stages_to_run"], "tts")

        self.set_state("completed")
        self.assertEqual(api.build_voiceover(self.project_id)["status"]["job_kind"], "voiceover-build")
        self.assertEqual(self.params()["stages_to_run"], "voiceover-build")

    def test_a_full_run_clears_a_previous_scoped_rerun(self) -> None:
        api.list_segments(self.project_id)
        api.regenerate_segment(self.project_id, "c001")
        self.assertIn("voiceover_chunks", self.params())

        self.set_state("completed")
        api.start_voiceover(self.project_id)

        self.assertNotIn("voiceover_chunks", self.params())
        self.assertEqual(self.params()["stages_to_run"], "voiceover")

    def test_every_mutation_is_refused_while_a_job_is_running(self) -> None:
        api.list_segments(self.project_id)
        for state in ("queued", "running"):
            self.set_state(state)
            for call in (
                lambda: api.start_voiceover(self.project_id),
                lambda: api.synthesize_missing_segments(self.project_id),
                lambda: api.build_voiceover(self.project_id),
                lambda: api.regenerate_segment(self.project_id, "c000"),
                lambda: api.delete_segment_audio(self.project_id, "c000"),
                lambda: self.upload("c000"),
            ):
                with self.assertRaises(api.HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 409)

    def test_listing_still_works_while_a_job_is_running(self) -> None:
        api.list_segments(self.project_id)
        self.set_state("running")
        payload = api.list_segments(self.project_id)
        self.assertTrue(payload["busy"])

    # ---------------------------------------------------------------- stopping

    def test_stop_asks_the_worker_to_shut_a_live_run_down(self) -> None:
        self.set_state("running")
        project = api.cancel_project(self.project_id)

        # The API cannot signal the worker's processes, so it leaves a request.
        self.assertEqual(project["status"]["state"], "cancelling")
        self.assertTrue((self.root / "cancel.request").exists())
        # Still busy: the run is being torn down and must not be raced.
        self.assertTrue(project["busy"])
        with self.assertRaises(api.HTTPException) as caught:
            api.start_voiceover(self.project_id)
        self.assertEqual(caught.exception.status_code, 409)

    def test_stop_resolves_queued_work_immediately(self) -> None:
        self.set_state("queued")
        project = api.cancel_project(self.project_id)

        # Nothing has started, so there is no process to wait for.
        self.assertEqual(project["status"]["state"], "cancelled")
        self.assertFalse((self.root / "cancel.request").exists())
        self.assertFalse(project["busy"])

    def test_stop_resets_a_run_that_already_died(self) -> None:
        # Exactly the case a worker restart leaves behind.
        self.set_state("running", age_seconds=api.RUN_STALE_AFTER_SECONDS + 30)
        project = api.cancel_project(self.project_id)

        self.assertEqual(project["status"]["state"], "cancelled")
        self.assertIn("already stopped", project["status"]["message"])
        self.assertFalse((self.root / "cancel.request").exists())
        self.assertNotIn("heartbeat", project["status"])

    def test_an_orphaned_run_stops_blocking_new_work_on_its_own(self) -> None:
        """The stuck-forever bug: no button press should be needed to recover."""
        self.set_state("running", age_seconds=api.RUN_STALE_AFTER_SECONDS + 30)
        status = api.read_json(self.root / "status.json", {})
        self.assertTrue(api.run_is_stale(status))
        self.assertFalse(api.project_is_busy(status))
        # No 409, because nothing is actually driving that run.
        self.assertEqual(api.start_voiceover(self.project_id)["status"]["state"], "queued")

    def test_a_long_quiet_stage_is_not_mistaken_for_a_dead_run(self) -> None:
        # Voiceover can run for hours between stage transitions; the heartbeat
        # is what keeps it from looking orphaned.
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        api.write_json(
            self.root / "status.json",
            {
                "project_id": self.project_id,
                "state": "running",
                "stage": "voiceover",
                "updated_at": old,
                "heartbeat": datetime.now(timezone.utc).isoformat(),
            },
        )
        status = api.read_json(self.root / "status.json", {})
        self.assertFalse(api.run_is_stale(status))
        self.assertTrue(api.project_is_busy(status))

    def test_stop_is_rejected_when_there_is_nothing_to_stop(self) -> None:
        for state in ("completed", "failed", "cancelled", "draft"):
            self.set_state(state)
            with self.assertRaisesRegex(api.HTTPException, "nothing to stop"):
                api.cancel_project(self.project_id)

    def test_queueing_discards_a_stop_left_over_from_a_previous_run(self) -> None:
        (self.root / "cancel.request").write_text("stale", encoding="utf-8")
        api.start_voiceover(self.project_id)
        # Otherwise the worker would kill the new run the moment it started.
        self.assertFalse((self.root / "cancel.request").exists())

    # ---------------------------------------------------------------- round-trip

    def test_saving_the_transcript_preserves_worker_owned_fields(self) -> None:
        api.list_segments(self.project_id)
        chunks = self.read_transcript()
        chunks[0]["audio"] = {"file": "work/segments/c000.ogg", "status": "ready"}
        api.write_improved_transcript(self.root, self.improved, chunks)

        reloaded = api.list_segments(self.project_id)
        self.assertEqual(reloaded["segments"][0]["chunk_id"], "c000")
        self.assertIn("audio", self.read_transcript()[0])
        self.assertEqual(self.read_transcript()[0]["dltrans"], "uno")
