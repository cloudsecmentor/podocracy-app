"""Tests for the per-chunk voiceover segment store.

Two things are covered here that are easy to get wrong and expensive to notice
late: the status rules that decide what gets regenerated, and the fact that the
portal API carries its own copy of those rules (`apps/app-api/segments.py`,
built from a different Docker context). The parity test fails on drift.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = WORKER_ROOT.parent
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(WORKER_ROOT / "processing_container"))

from common import segment_store as store


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_store = load_module("api_segments", REPO_ROOT / "apps" / "app-api" / "segments.py")

try:
    voiceover = load_module("pd050_voiceover", WORKER_ROOT / "processing_container" / "pd-050-voiceover.py")
except Exception as exc:  # pragma: no cover - container-only dependencies
    voiceover = None
    VOICEOVER_IMPORT_ERROR = str(exc)


def chunk(chunk_id, start, end="", imp="hello"):
    item = {"chunk_id": chunk_id, "start": start, "imp": imp}
    if end:
        item["end"] = end
    return item


def ready_entry(text, voice_hash, source=store.SOURCE_TTS, filename="c000.ogg"):
    return {
        "file": filename,
        "source": source,
        "status": store.STATUS_READY,
        "text_hash": store.text_hash(text),
        "voice_hash": voice_hash,
    }


class ChunkIdTests(unittest.TestCase):
    def test_assigns_ids_by_position_when_none_exist(self):
        chunks = [{"start": "0000"}, {"start": "0010"}, {"start": "0020"}]
        self.assertTrue(store.assign_chunk_ids(chunks))
        self.assertEqual([item["chunk_id"] for item in chunks], ["c000", "c001", "c002"])

    def test_keeps_existing_ids_and_is_idempotent(self):
        chunks = [chunk("c005", "0000"), {"start": "0010"}]
        store.assign_chunk_ids(chunks)
        self.assertEqual([item["chunk_id"] for item in chunks], ["c005", "c006"])
        self.assertFalse(store.assign_chunk_ids(chunks))

    def test_never_reuses_a_retired_id(self):
        chunks = [chunk("c000", "0000"), chunk("c001", "0010"), chunk("c002", "0020")]
        del chunks[1]
        chunks.insert(1, {"start": "0005"})
        store.assign_chunk_ids(chunks)
        self.assertEqual([item["chunk_id"] for item in chunks], ["c000", "c003", "c002"])

    def test_duplicate_ids_keep_the_first_and_reassign_the_rest(self):
        chunks = [chunk("c000", "0000"), chunk("c000", "0010")]
        self.assertTrue(store.assign_chunk_ids(chunks))
        self.assertEqual([item["chunk_id"] for item in chunks], ["c000", "c001"])

    def test_malformed_ids_are_replaced(self):
        chunks = [{"chunk_id": "nope", "start": "0000"}, {"chunk_id": 7, "start": "0010"}]
        store.assign_chunk_ids(chunks)
        self.assertEqual([item["chunk_id"] for item in chunks], ["c000", "c001"])


class StagingNameTests(unittest.TestCase):
    def test_uses_start_and_end_when_both_present(self):
        self.assertEqual(store.staging_basename(chunk("c000", "0738", "0749")), "0738-0749")

    def test_blank_end_is_treated_as_absent(self):
        # `0738-.ogg` fails the assembler's filename check and would be dropped.
        self.assertEqual(store.staging_basename({"chunk_id": "c000", "start": "0738", "end": ""}), "0738")

    def test_missing_start_is_an_error(self):
        with self.assertRaises(ValueError):
            store.staging_basename({"chunk_id": "c000", "start": ""})


class HashTests(unittest.TestCase):
    def test_text_hash_ignores_surrounding_whitespace(self):
        self.assertEqual(store.text_hash("  hello "), store.text_hash("hello"))

    def test_text_hash_is_content_sensitive(self):
        self.assertNotEqual(store.text_hash("hello"), store.text_hash("hello."))

    def test_voice_hash_tracks_synthesis_params_only(self):
        base = {"tts_api": "vibevoice", "voice": "SEBBE", "vibevoice_model": "7B"}
        self.assertEqual(store.voice_hash(base), store.voice_hash({**base, "language": "RU"}))
        self.assertNotEqual(store.voice_hash(base), store.voice_hash({**base, "voice": "OTHER"}))
        self.assertNotEqual(store.voice_hash(base), store.voice_hash({**base, "vibevoice_speed": "1.1"}))


class ChunkTextTests(unittest.TestCase):
    def test_improved_text_wins(self):
        self.assertEqual(store.chunk_text({"imp": "improved", "dltrans": "translated"}), "improved")

    def test_blanked_improved_text_silences_the_chunk(self):
        # Clearing the field in the editor is how a chunk is muted; falling back
        # to the translation would voice it anyway.
        self.assertEqual(store.chunk_text({"imp": "", "dltrans": "translated"}), "")
        self.assertEqual(store.chunk_text({"imp": "   ", "dltrans": "translated"}), "")

    def test_absent_improved_key_falls_back_to_the_translation(self):
        # The improve stage never ran, so the translation is all there is.
        self.assertEqual(store.chunk_text({"dltrans": "translated"}), "translated")


class SegmentStatusTests(unittest.TestCase):
    def setUp(self):
        self.voice = store.voice_hash({"tts_api": "vibevoice", "voice": "SEBBE"})
        self.other_voice = store.voice_hash({"tts_api": "vibevoice", "voice": "OTHER"})

    def status(self, entry, text="hello", voice=None, audio_exists=True):
        return store.segment_status(
            entry,
            text=text,
            current_text_hash=store.text_hash(text),
            current_voice_hash=voice or self.voice,
            audio_exists=audio_exists,
        )

    def test_empty_text_is_skipped(self):
        self.assertEqual(self.status(None, text="   "), store.STATUS_SKIPPED)

    def test_no_entry_is_missing(self):
        self.assertEqual(self.status(None), store.STATUS_MISSING)

    def test_current_generated_audio_is_ready(self):
        self.assertEqual(self.status(ready_entry("hello", self.voice)), store.STATUS_READY)

    def test_generated_audio_goes_stale_on_text_change(self):
        self.assertEqual(self.status(ready_entry("different", self.voice)), store.STATUS_STALE)

    def test_generated_audio_goes_stale_on_voice_change(self):
        self.assertEqual(self.status(ready_entry("hello", self.other_voice)), store.STATUS_STALE)

    def test_recording_survives_a_voice_change(self):
        entry = ready_entry("hello", self.other_voice, source=store.SOURCE_RECORDING)
        self.assertEqual(self.status(entry), store.STATUS_READY)

    def test_recording_is_flagged_stale_on_text_change(self):
        entry = ready_entry("different", self.voice, source=store.SOURCE_RECORDING)
        self.assertEqual(self.status(entry), store.STATUS_STALE)

    def test_entry_without_its_file_is_missing(self):
        self.assertEqual(self.status(ready_entry("hello", self.voice), audio_exists=False), store.STATUS_MISSING)

    def test_failed_and_pending_states_are_preserved(self):
        self.assertEqual(self.status({"status": store.STATUS_FAILED}), store.STATUS_FAILED)
        self.assertEqual(self.status({"status": store.STATUS_PENDING_INGEST}), store.STATUS_PENDING_INGEST)


class NeedsSynthesisTests(unittest.TestCase):
    def test_gaps_and_stale_generated_audio_are_regenerated(self):
        self.assertTrue(store.needs_synthesis(store.STATUS_MISSING, None))
        self.assertTrue(store.needs_synthesis(store.STATUS_STALE, {"source": store.SOURCE_TTS}))
        self.assertTrue(store.needs_synthesis(store.STATUS_FAILED, {"source": store.SOURCE_TTS}))

    def test_current_audio_is_left_alone(self):
        self.assertFalse(store.needs_synthesis(store.STATUS_READY, {"source": store.SOURCE_TTS}))
        self.assertFalse(store.needs_synthesis(store.STATUS_SKIPPED, None))

    def test_recordings_are_never_regenerated_automatically(self):
        # The user cannot cheaply reproduce a recording, so a stale flag is
        # advisory only.
        for status in (store.STATUS_MISSING, store.STATUS_STALE, store.STATUS_FAILED):
            self.assertFalse(store.needs_synthesis(status, {"source": store.SOURCE_RECORDING}))


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_missing_index_reads_as_empty(self):
        self.assertEqual(store.load_index(self.root), store.empty_index())

    def test_round_trip(self):
        index = store.empty_index()
        index["segments"]["c000"] = {"file": "c000.ogg"}
        store.save_index(self.root, index)
        self.assertEqual(store.load_index(self.root)["segments"]["c000"]["file"], "c000.ogg")

    def test_corrupt_index_does_not_crash_the_run(self):
        store.index_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        store.index_path(self.root).write_text("{ not json", encoding="utf-8")
        self.assertEqual(store.load_index(self.root), store.empty_index())

    def test_audio_path_prefers_canonical_then_falls_back_to_raw(self):
        segments_dir = store.store_dir(self.root)
        (segments_dir / "raw").mkdir(parents=True, exist_ok=True)
        (segments_dir / "raw" / "c000.webm").write_bytes(b"raw")
        entry = {"file": "c000.ogg", "raw_file": "raw/c000.webm"}
        self.assertEqual(store.audio_path(self.root, entry).name, "c000.webm")
        (segments_dir / "c000.ogg").write_bytes(b"canonical")
        self.assertEqual(store.audio_path(self.root, entry).name, "c000.ogg")

    def test_zero_byte_files_do_not_count_as_audio(self):
        segments_dir = store.store_dir(self.root)
        segments_dir.mkdir(parents=True, exist_ok=True)
        (segments_dir / "c000.ogg").write_bytes(b"")
        self.assertIsNone(store.audio_path(self.root, {"file": "c000.ogg"}))


class ApiMirrorParityTests(unittest.TestCase):
    """`apps/app-api/segments.py` is a hand-kept copy; catch drift here."""

    def test_shared_constants_match(self):
        for name in (
            "SEGMENT_STORE_VERSION",
            "STATUS_READY",
            "STATUS_STALE",
            "STATUS_MISSING",
            "STATUS_SKIPPED",
            "STATUS_FAILED",
            "STATUS_PENDING_INGEST",
            "SOURCE_TTS",
            "SOURCE_RECORDING",
            "CANONICAL_EXTENSION",
            "RAW_UPLOAD_EXTENSIONS",
            "VOICE_PARAM_KEYS",
        ):
            self.assertEqual(getattr(store, name), getattr(api_store, name), name)

    def test_chunk_text_rules_match(self):
        for chunk_value in (
            {"imp": "improved", "dltrans": "translated"},
            {"imp": "", "dltrans": "translated"},
            {"dltrans": "translated"},
            {},
        ):
            self.assertEqual(store.chunk_text(chunk_value), api_store.chunk_text(chunk_value), chunk_value)

    def test_hashes_match(self):
        params = {"tts_api": "vibevoice", "voice": "SEBBE", "vibevoice_speed": "1.1"}
        self.assertEqual(store.voice_hash(params), api_store.voice_hash(params))
        self.assertEqual(store.text_hash(" text "), api_store.text_hash(" text "))

    def test_chunk_id_assignment_matches(self):
        source = [{"start": "0000"}, {"chunk_id": "c004", "start": "0010"}, {"chunk_id": "c004", "start": "0020"}]
        mine = json.loads(json.dumps(source))
        theirs = json.loads(json.dumps(source))
        store.assign_chunk_ids(mine)
        api_store.assign_chunk_ids(theirs)
        self.assertEqual(mine, theirs)

    def test_status_matrix_matches(self):
        voice = store.voice_hash({"voice": "A"})
        cases = [
            (None, "hello", True),
            (None, "", True),
            (ready_entry("hello", voice), "hello", True),
            (ready_entry("hello", voice), "changed", True),
            (ready_entry("hello", "other"), "hello", True),
            (ready_entry("hello", "other", source=store.SOURCE_RECORDING), "hello", True),
            (ready_entry("hello", voice), "hello", False),
            ({"status": store.STATUS_FAILED}, "hello", True),
            ({"status": store.STATUS_PENDING_INGEST}, "hello", True),
        ]
        for entry, text, exists in cases:
            kwargs = dict(
                text=text,
                current_text_hash=store.text_hash(text),
                current_voice_hash=voice,
                audio_exists=exists,
            )
            self.assertEqual(
                store.segment_status(entry, **kwargs),
                api_store.segment_status(entry, **kwargs),
                f"{entry} / {text!r} / exists={exists}",
            )

    def test_describe_chunks_matches(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            segments_dir = store.store_dir(root)
            segments_dir.mkdir(parents=True, exist_ok=True)
            (segments_dir / "c000.ogg").write_bytes(b"audio")
            params = {"tts_api": "openai", "voice": "alloy"}
            index = store.empty_index()
            index["segments"]["c000"] = ready_entry("one", store.voice_hash(params))
            store.save_index(root, index)
            chunks = [chunk("c000", "0000", imp="one"), chunk("c001", "0010", imp="two")]
            self.assertEqual(
                store.describe_chunks(root, chunks, params),
                api_store.describe_chunks(root, chunks, params),
            )


@unittest.skipIf(voiceover is None, "pd-050 dependencies are container-only")
class BuildStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.params = {"tts_api": "openai", "voice": "alloy", "voiceover_tempo": 1.2}
        self.voice = store.voice_hash(self.params)
        store.store_dir(self.root).mkdir(parents=True, exist_ok=True)
        patcher = patch.object(voiceover, "get_params", return_value=1.2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_segment(self, chunk_id, text, source=store.SOURCE_TTS):
        path = store.store_dir(self.root) / f"{chunk_id}.ogg"
        path.write_bytes(b"audio")
        index = store.load_index(self.root)
        index["segments"][chunk_id] = ready_entry(text, self.voice, source=source, filename=path.name)
        store.save_index(self.root, index)

    def test_stages_under_timing_names_and_tempo_by_source(self):
        self.write_segment("c000", "one")
        self.write_segment("c001", "two", source=store.SOURCE_RECORDING)
        chunks = [chunk("c000", "0000", "0010", imp="one"), chunk("c001", "0010", "0020", imp="two")]

        staging, tempo = voiceover.stage_segments_for_build(self.root, chunks, self.params)

        self.assertEqual(sorted(item.name for item in staging.glob("*.ogg")), ["0000-0010.ogg", "0010-0020.ogg"])
        self.assertEqual(tempo["0000-0010.ogg"], 1.2)
        # Speeding up the user's own recording is never wanted.
        self.assertEqual(tempo["0010-0020.ogg"], 1.0)

    def test_chunk_without_end_is_staged_under_its_start(self):
        self.write_segment("c000", "one")
        staging, _ = voiceover.stage_segments_for_build(self.root, [chunk("c000", "0000", imp="one")], self.params)
        self.assertEqual([item.name for item in staging.glob("*.ogg")], ["0000.ogg"])

    def test_missing_audio_is_skipped_but_the_build_continues(self):
        self.write_segment("c000", "one")
        chunks = [chunk("c000", "0000", "0010", imp="one"), chunk("c001", "0010", "0020", imp="two")]
        staging, tempo = voiceover.stage_segments_for_build(self.root, chunks, self.params)
        self.assertEqual([item.name for item in staging.glob("*.ogg")], ["0000-0010.ogg"])
        self.assertEqual(len(tempo), 1)

    def test_duplicate_timings_fail_loudly(self):
        # The legacy assembler globs by filename, so a collision would silently
        # drop one of the two chunks from the mix.
        self.write_segment("c000", "one")
        self.write_segment("c001", "two")
        chunks = [chunk("c000", "0000", "0010", imp="one"), chunk("c001", "0000", "0010", imp="two")]
        with self.assertRaises(ValueError) as caught:
            voiceover.stage_segments_for_build(self.root, chunks, self.params)
        self.assertIn("c000", str(caught.exception))
        self.assertIn("c001", str(caught.exception))

    def test_nothing_to_assemble_is_an_error(self):
        with self.assertRaises(ValueError):
            voiceover.stage_segments_for_build(self.root, [chunk("c000", "0000", imp="one")], self.params)

    def test_stale_audio_is_still_assembled_but_warned_about(self):
        # A hole in the mix is worse than audio that lags the text by one edit,
        # so stale chunks ship; the log and the editor badge say so.
        self.write_segment("c000", "old text")
        with self.assertLogs(level="WARNING") as logs:
            staging, _ = voiceover.stage_segments_for_build(
                self.root, [chunk("c000", "0000", imp="new text")], self.params
            )
        self.assertEqual([item.name for item in staging.glob("*.ogg")], ["0000.ogg"])
        self.assertIn("predates the current text", "\n".join(logs.output))


@unittest.skipIf(voiceover is None, "pd-050 dependencies are container-only")
class VoiceoverHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_parse_chunk_ids_accepts_lists_and_strings(self):
        self.assertEqual(voiceover.parse_chunk_ids("c001, c002"), {"c001", "c002"})
        self.assertEqual(voiceover.parse_chunk_ids(["c003"]), {"c003"})
        self.assertEqual(voiceover.parse_chunk_ids(None), set())

    def test_parse_chunk_ids_rejects_anything_that_could_escape_the_store(self):
        for value in ("../etc/passwd", "c1", "chunk-1", "c001;rm"):
            with self.assertRaises(ValueError):
                voiceover.parse_chunk_ids(value)

    def test_redo_segment_key_maps_to_a_chunk_id(self):
        chunks = [chunk("c000", "0000", "0010"), chunk("c001", "0738", "0749")]
        self.assertEqual(voiceover.chunk_id_for_segment_key(chunks, "0738-0749"), "c001")
        with self.assertRaises(ValueError):
            voiceover.chunk_id_for_segment_key(chunks, "9999-9999")

    def test_resolve_tempo_falls_back_on_junk(self):
        self.assertEqual(voiceover.resolve_tempo({"voiceover_tempo": "1.4"}, "voiceover_tempo", 1.2), 1.4)
        self.assertEqual(voiceover.resolve_tempo({"voiceover_tempo": ""}, "voiceover_tempo", 1.2), 1.2)
        self.assertEqual(voiceover.resolve_tempo({"voiceover_tempo": "fast"}, "voiceover_tempo", 1.2), 1.2)

    def test_orphaned_audio_is_moved_aside_not_deleted(self):
        segments_dir = store.store_dir(self.root)
        segments_dir.mkdir(parents=True, exist_ok=True)
        (segments_dir / "c009.ogg").write_bytes(b"audio")
        index = store.empty_index()
        index["segments"]["c009"] = {"file": "c009.ogg", "source": store.SOURCE_RECORDING}
        store.save_index(self.root, index)

        voiceover.retire_orphaned_segments(self.root, [chunk("c000", "0000")])

        self.assertFalse((segments_dir / "c009.ogg").exists())
        self.assertTrue((store.orphan_dir(self.root) / "c009.ogg").exists())
        self.assertNotIn("c009", store.load_index(self.root)["segments"])

    def test_match_files_to_chunks_uses_timings_then_position(self):
        with tempfile.TemporaryDirectory() as name:
            source = Path(name)
            (source / "0738-0749.ogg").write_bytes(b"a")
            (source / "unmatched.ogg").write_bytes(b"b")
            chunks = [chunk("c000", "0738", "0749"), chunk("c001", "0800", "0810")]
            matched = voiceover.match_files_to_chunks(list(source.glob("*.ogg")), chunks)
            self.assertEqual(matched["c000"].name, "0738-0749.ogg")
            self.assertEqual(matched["c001"].name, "unmatched.ogg")


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(voiceover is None, "pd-050 dependencies are container-only")
class SynthesizeStageTests(unittest.TestCase):
    """End to end over `pd-050-voiceover.py --mode synthesize`, with the TTS call
    stubbed. Covers the behaviour the editor depends on: stable ids, a populated
    store, audio mirrored onto each chunk, and gap-filling that skips what is
    already current."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)

        # get_params() resolves parameters.json relative to the working directory.
        layout = self.run_dir / "backend"
        layout.mkdir()
        (layout / "processing_container").symlink_to(WORKER_ROOT / "processing_container", target_is_directory=True)
        self.previous_cwd = Path.cwd()
        import os

        os.chdir(self.run_dir)
        self.addCleanup(lambda: os.chdir(self.previous_cwd))

        self.project = self.run_dir / "projects" / "project-test"
        (self.project / "input").mkdir(parents=True)
        (self.project / "config").mkdir(parents=True)
        self.source = self.project / "input" / "source.mp3"
        self.source.write_bytes(b"audio")
        self.improved = self.project / "input" / "source.improved.json"
        self.params_file = self.project / "input" / "source.params.json"
        self.write_transcript(
            [
                {"start": "0000", "end": "0010", "text": "one", "dltrans": "uno", "imp": "first"},
                {"start": "0010", "end": "0020", "text": "two", "dltrans": "dos", "imp": "second"},
                {"start": "0020", "end": "0030", "text": "three", "dltrans": "tres", "imp": ""},
            ]
        )
        self.params_file.write_text(
            json.dumps({"tts_api": "openai", "voice": "alloy", "sleep_time_tts": 0, "filename": "source.mp3"}),
            encoding="utf-8",
        )

        patcher = patch.dict("os.environ", {"PODOCRACY_PROJECT_DIR": str(self.project)})
        patcher.start()
        self.addCleanup(patcher.stop)
        logging_patch = patch.object(voiceover, "setup_logging_with_appinsights", lambda *a, **k: None)
        logging_patch.start()
        self.addCleanup(logging_patch.stop)

        self.synthesized = []

        def fake_tts(path, text, speech_file_path, voice):
            self.synthesized.append(text)
            Path(speech_file_path).write_bytes(b"generated-" + text.encode())

        tts_patch = patch.object(voiceover, "generate_openai_tts", side_effect=fake_tts)
        tts_patch.start()
        self.addCleanup(tts_patch.stop)

    def write_transcript(self, chunks):
        self.improved.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")

    def read_transcript(self):
        return json.loads(self.improved.read_text(encoding="utf-8"))

    def run_synthesize(self):
        voiceover.main(str(self.source), mode="synthesize")

    def test_populates_the_store_and_mirrors_audio_onto_each_chunk(self):
        self.run_synthesize()

        self.assertEqual(self.synthesized, ["first", "second"])
        chunks = self.read_transcript()
        self.assertEqual([item["chunk_id"] for item in chunks], ["c000", "c001", "c002"])
        self.assertEqual(chunks[0]["audio"]["status"], "ready")
        self.assertEqual(chunks[0]["audio"]["source"], "tts")
        self.assertEqual(chunks[0]["audio"]["file"], "work/segments/c000.ogg")
        # An empty chunk is expected to have no audio, not to be a gap.
        self.assertNotIn("audio", chunks[2])
        self.assertEqual((store.store_dir(self.project) / "c000.ogg").read_bytes(), b"generated-first")
        self.assertEqual(self.read_transcript()[0]["dltrans"], "uno")

    def test_second_run_regenerates_nothing(self):
        self.run_synthesize()
        self.synthesized.clear()
        self.run_synthesize()
        self.assertEqual(self.synthesized, [])

    def test_only_the_edited_chunk_is_regenerated(self):
        self.run_synthesize()
        chunks = self.read_transcript()
        chunks[1]["imp"] = "second, revised"
        self.write_transcript(chunks)
        self.synthesized.clear()

        self.run_synthesize()

        self.assertEqual(self.synthesized, ["second, revised"])

    def test_a_recording_is_left_alone_by_a_gap_filling_run(self):
        self.run_synthesize()
        index = store.load_index(self.project)
        index["segments"]["c000"]["source"] = store.SOURCE_RECORDING
        index["segments"]["c000"]["text_hash"] = store.text_hash("something else")
        store.save_index(self.project, index)
        self.synthesized.clear()

        self.run_synthesize()

        self.assertEqual(self.synthesized, [])
        self.assertEqual(self.read_transcript()[0]["audio"]["status"], "stale")

    def test_scoped_rerun_regenerates_exactly_that_chunk_and_unscopes_itself(self):
        self.run_synthesize()
        self.synthesized.clear()
        params = json.loads(self.params_file.read_text(encoding="utf-8"))
        params["voiceover_chunks"] = ["c001"]
        self.params_file.write_text(json.dumps(params), encoding="utf-8")

        self.run_synthesize()

        self.assertEqual(self.synthesized, ["second"])
        self.assertNotIn("voiceover_chunks", json.loads(self.params_file.read_text(encoding="utf-8")))

    def test_a_failing_chunk_is_recorded_and_does_not_lose_the_others(self):
        def flaky(path, text, speech_file_path, voice):
            if text == "first":
                raise RuntimeError("server said no")
            Path(speech_file_path).write_bytes(b"generated")

        with patch.object(voiceover, "generate_openai_tts", side_effect=flaky):
            with self.assertRaisesRegex(RuntimeError, "c000"):
                self.run_synthesize()

        chunks = self.read_transcript()
        self.assertEqual(chunks[0]["audio"]["status"], "failed")
        self.assertIn("server said no", chunks[0]["audio"]["error"])
        # The chunk that did succeed is persisted, so a retry only redoes c000.
        self.assertEqual(chunks[1]["audio"]["status"], "ready")

    def test_a_failed_regeneration_keeps_the_previous_take(self):
        self.run_synthesize()
        original = (store.store_dir(self.project) / "c000.ogg").read_bytes()
        chunks = self.read_transcript()
        chunks[0]["imp"] = "first, revised"
        self.write_transcript(chunks)

        def always_fails(path, text, speech_file_path, voice):
            Path(speech_file_path).write_bytes(b"truncated")
            raise RuntimeError("connection reset")

        with patch.object(voiceover, "generate_openai_tts", side_effect=always_fails):
            with self.assertRaises(RuntimeError):
                self.run_synthesize()

        # The old audio is intact and still usable; the error says the retry failed.
        self.assertEqual((store.store_dir(self.project) / "c000.ogg").read_bytes(), original)
        audio = self.read_transcript()[0]["audio"]
        self.assertEqual(audio["status"], "stale")
        self.assertIn("connection reset", audio["error"])
        self.assertEqual(list(store.store_dir(self.project).glob("*.partial.*")), [])

    def test_a_first_time_failure_is_reported_as_failed(self):
        def always_fails(path, text, speech_file_path, voice):
            raise RuntimeError("no server")

        with patch.object(voiceover, "generate_openai_tts", side_effect=always_fails):
            with self.assertRaises(RuntimeError):
                self.run_synthesize()

        self.assertEqual(self.read_transcript()[0]["audio"]["status"], "failed")

    def test_silently_empty_tts_output_counts_as_a_failure(self):
        def writes_nothing(path, text, speech_file_path, voice):
            Path(speech_file_path).write_bytes(b"")

        with patch.object(voiceover, "generate_openai_tts", side_effect=writes_nothing):
            with self.assertRaises(RuntimeError):
                self.run_synthesize()

        self.assertEqual(self.read_transcript()[0]["audio"]["status"], "failed")

    def test_deleted_chunk_audio_is_retired_not_destroyed(self):
        self.run_synthesize()
        chunks = self.read_transcript()
        del chunks[0]
        self.write_transcript(chunks)

        self.run_synthesize()

        self.assertTrue((store.orphan_dir(self.project) / "c000.ogg").exists())
        self.assertNotIn("c000", store.load_index(self.project)["segments"])
