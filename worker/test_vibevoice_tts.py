from __future__ import annotations

import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

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


VIBEVOICE_ENV_KEYS = (
    "VIBEVOICE_BASE_URL",
    "VIBEVOICE_API_KEY",
    "VIBEVOICE_TTS_MODEL",
    "VIBEVOICE_TTS_VOICE",
    "VIBEVOICE_TIMEOUT_SECONDS",
    "VIBEVOICE_CFG_SCALE",
    "VIBEVOICE_SPEED",
)


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"audio", payload=None, text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def clean_env(**overrides: str) -> dict[str, str]:
    env = {key: "" for key in VIBEVOICE_ENV_KEYS}
    env.update(overrides)
    return env


class VibeVoiceSynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.output = Path(self.temp_dir.name) / "0000-0010.mp3"

    def test_posts_expected_body_and_writes_audio(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://host.docker.internal:8000/v1/")
        params = {"voice": "SEBBE", "vibevoice_model": "7B", "vibevoice_speed": "1.25"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=FakeResponse(content=b"mp3-bytes")) as post:
                local_worker.synthesize_vibevoice_tts("Hello there", self.output, params)

        url = post.call_args[0][0]
        payload = post.call_args.kwargs["json"]
        self.assertEqual(url, "http://host.docker.internal:8000/v1/audio/speech")
        self.assertEqual(payload["input"], "Hello there")
        self.assertEqual(payload["voice"], "SEBBE")
        self.assertEqual(payload["model"], "7B")
        self.assertEqual(payload["response_format"], "mp3")
        self.assertEqual(payload["speed"], 1.25)
        self.assertEqual(post.call_args.kwargs["timeout"], (10.0, 900.0))
        self.assertEqual(self.output.read_bytes(), b"mp3-bytes")

    def test_authorization_header_tracks_api_key(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=FakeResponse()) as post:
                local_worker.synthesize_vibevoice_tts("text", self.output, {})
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1", VIBEVOICE_API_KEY="secret-token")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=FakeResponse()) as post:
                local_worker.synthesize_vibevoice_tts("text", self.output, {})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret-token")

    def test_cfg_scale_omitted_unless_configured(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=FakeResponse()) as post:
                local_worker.synthesize_vibevoice_tts("text", self.output, {"vibevoice_cfg_scale": ""})
        self.assertNotIn("cfg_scale", post.call_args.kwargs["json"])

        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=FakeResponse()) as post:
                local_worker.synthesize_vibevoice_tts("text", self.output, {"vibevoice_cfg_scale": "1.7"})
        self.assertEqual(post.call_args.kwargs["json"]["cfg_scale"], 1.7)

    def test_error_response_surfaces_status_and_detail(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        response = FakeResponse(status_code=400, content=b"", payload={"detail": "unknown voice 'NOPE'"})
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=response):
                with self.assertRaisesRegex(RuntimeError, r"HTTP 400: unknown voice 'NOPE'"):
                    local_worker.synthesize_vibevoice_tts("text", self.output, {})
        self.assertFalse(self.output.exists())

    def test_empty_body_raises_instead_of_writing_zero_byte_file(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=FakeResponse(content=b"")):
                with self.assertRaisesRegex(RuntimeError, "empty audio response"):
                    local_worker.synthesize_vibevoice_tts("text", self.output, {})
        self.assertFalse(self.output.exists())

    def test_missing_base_url_names_the_variable(self) -> None:
        with patch.dict(os.environ, clean_env(), clear=False):
            with self.assertRaisesRegex(RuntimeError, "VIBEVOICE_BASE_URL is not set"):
                local_worker.synthesize_vibevoice_tts("text", self.output, {})

    def test_non_http_scheme_is_rejected(self) -> None:
        with patch.dict(os.environ, clean_env(VIBEVOICE_BASE_URL="file:///etc/passwd"), clear=False):
            with self.assertRaisesRegex(RuntimeError, "must use http or https"):
                local_worker.synthesize_vibevoice_tts("text", self.output, {})

    def test_error_body_is_truncated(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        response = FakeResponse(status_code=500, content=b"", text="x" * 5000)
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "post", return_value=response):
                with self.assertRaises(RuntimeError) as caught:
                    local_worker.synthesize_vibevoice_tts("text", self.output, {})
        self.assertLess(len(str(caught.exception)), local_worker.VIBEVOICE_ERROR_BODY_LIMIT + 100)


class VibeVoicePreflightTests(unittest.TestCase):
    def test_unreachable_server_names_url_and_fix(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://host.docker.internal:8000/v1")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "get", side_effect=requests.ConnectionError("refused")):
                with self.assertRaisesRegex(RuntimeError, "not reachable at http://host.docker.internal:8000/v1"):
                    local_worker.check_vibevoice_server(logging.getLogger("test"))

    def test_healthy_server_passes(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "get", return_value=FakeResponse()) as get:
                local_worker.check_vibevoice_server(logging.getLogger("test"))
        self.assertEqual(get.call_args[0][0], "http://127.0.0.1:8000/v1/health")

    def test_falls_back_to_root_health_path(self) -> None:
        env = clean_env(VIBEVOICE_BASE_URL="http://127.0.0.1:8000/v1")
        responses = [FakeResponse(status_code=404, content=b""), FakeResponse()]
        with patch.dict(os.environ, env, clear=False):
            with patch.object(local_worker.requests, "get", side_effect=responses) as get:
                local_worker.check_vibevoice_server(logging.getLogger("test"))
        self.assertEqual(
            [call[0][0] for call in get.call_args_list],
            ["http://127.0.0.1:8000/v1/health", "http://127.0.0.1:8000/health"],
        )


class VibeVoiceRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project = Path(self.temp_dir.name)

    def segments(self) -> list[dict]:
        return [
            {"start": "0000", "end": "0010", "imp": "First line", "end_seconds": 10},
            {"start": "0010", "end": "0020", "imp": "Second line", "end_seconds": 20},
        ]

    def test_vibevoice_path_is_used_and_no_sleep_between_segments(self) -> None:
        params = {"tts_api": "vibevoice", "improved_text_key": "imp", "sleep_time_tts": 0.5}
        logger = logging.getLogger("test")

        with (
            patch.object(local_worker, "synthesize_vibevoice_tts") as vibevoice,
            patch.object(local_worker, "synthesize_openai_tts") as openai,
            patch.object(local_worker, "synthesize_elevenlabs_tts") as elevenlabs,
            patch.object(local_worker.time, "sleep") as sleep,
        ):
            synthesized = local_worker.synthesize_segments(self.segments(), params, self.project, logger)

        self.assertEqual(vibevoice.call_count, 2)
        openai.assert_not_called()
        elevenlabs.assert_not_called()
        self.assertEqual(sleep.call_args_list, [((0.0,), {}), ((0.0,), {})])
        self.assertTrue(all(item["tts_path"].endswith(".mp3") for item in synthesized))

    def test_openai_remains_the_fallback_for_unknown_providers(self) -> None:
        params = {"tts_api": "nonsense", "improved_text_key": "imp", "sleep_time_tts": 0}
        with (
            patch.object(local_worker, "synthesize_vibevoice_tts") as vibevoice,
            patch.object(local_worker, "synthesize_openai_tts") as openai,
            patch.object(local_worker.time, "sleep"),
        ):
            local_worker.synthesize_segments(self.segments(), params, self.project, logging.getLogger("test"))

        self.assertEqual(openai.call_count, 2)
        vibevoice.assert_not_called()


if __name__ == "__main__":
    unittest.main()
