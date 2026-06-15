from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydub import AudioSegment


WORKER_VERSION = "local-worker-0.1.1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def setup_logger(project: Path) -> logging.Logger:
    logs_dir = project / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"podocracy.{project.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(logs_dir / "orchestrator.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def update_status(project: Path, state: str, stage: str, progress: int, message: str = "", error: str | None = None) -> None:
    status = {
        "project_id": project.name,
        "state": state,
        "stage": stage,
        "progress": progress,
        "message": message,
        "updated_at": now_iso(),
    }
    if error:
        status["error"] = error
    write_json(project / "status.json", status)


def append_stage(manifest: dict[str, Any], name: str, status: str, started_at: str, ended_at: str, detail: str = "") -> None:
    manifest.setdefault("stages", []).append(
        {
            "name": name,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "detail": detail,
        }
    )


def get_source(project: Path, metadata: dict[str, Any]) -> Path:
    relative = metadata.get("source_path")
    if relative:
        source = project / relative
        if source.exists():
            return source
    candidates = list((project / "input").glob("*"))
    candidates = [item for item in candidates if item.is_file() and not item.name.endswith(".json")]
    if not candidates:
        raise FileNotFoundError("No source file found in project input folder")
    return candidates[0]


def ensure_mp3(source: Path, work_dir: Path, logger: logging.Logger) -> Path:
    mp3_path = work_dir / f"{source.stem}.source.mp3"
    if source.suffix.lower() == ".mp3":
        shutil.copy2(source, mp3_path)
        return mp3_path

    logger.info("Converting source to mp3: %s", source)
    audio = AudioSegment.from_file(source)
    audio.export(mp3_path, format="mp3", bitrate="192k")
    return mp3_path


def model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return json.loads(value.model_dump_json())


def get_timing_format(max_end_seconds: float) -> str:
    return "hhmmss" if max_end_seconds > 6000 else "mmss"


def seconds_to_timecode(seconds: float, timing_format: str = "mmss") -> str:
    total_seconds = max(0, round(float(seconds)))
    if timing_format == "hhmmss":
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds_remainder = divmod(remainder, 60)
        return f"{hours:02d}{minutes:02d}{seconds_remainder:02d}"
    minutes, seconds_remainder = divmod(total_seconds, 60)
    return f"{minutes:02d}{seconds_remainder:02d}"


def mmss_to_seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        return int(text[:2]) * 60 + int(text[2:])
    if len(text) == 6 and text.isdigit():
        return int(text[:2]) * 3600 + int(text[2:4]) * 60 + int(text[4:])
    return float(text)


def segment_start_seconds(segment: dict[str, Any]) -> float:
    return float(segment.get("start_seconds", mmss_to_seconds(segment.get("start", 0))))


def segment_end_seconds(segment: dict[str, Any]) -> float:
    return float(segment.get("end_seconds", mmss_to_seconds(segment.get("end", segment_start_seconds(segment)))))


def transcribe(source_mp3: Path, project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    logger.info("Transcribing with OpenAI model %s", model)
    with source_mp3.open("rb") as audio_file:
        try:
            transcript = client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        except TypeError:
            audio_file.seek(0)
            transcript = client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                response_format="verbose_json",
            )

    data = model_dump(transcript)
    write_json(project / "work" / "source.raw.json", data)
    text = str(data.get("text") or "").strip()
    segments = data.get("segments") or []
    if not segments and text:
        duration = AudioSegment.from_file(source_mp3).duration_seconds
        segments = [{"id": 0, "start": 0.0, "end": duration, "text": text}]

    max_end_seconds = max([float(segment.get("end", 0.0)) for segment in segments] or [0.0])
    timing_format = get_timing_format(max_end_seconds)
    cleaned = []
    for index, segment in enumerate(segments):
        segment_text = str(segment.get("text") or "").strip()
        if not segment_text:
            continue
        start_seconds = float(segment.get("start", 0.0))
        end_seconds = float(segment.get("end", 0.0))
        cleaned.append(
            {
                "id": int(segment.get("id", index)),
                "start": seconds_to_timecode(start_seconds, timing_format),
                "end": seconds_to_timecode(end_seconds, timing_format),
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "text": segment_text,
            }
        )
    write_json(project / "work" / "source.combined.json", cleaned)
    (project / "output" / "source.transcript.txt").write_text(text, encoding="utf-8")
    return cleaned


def normalize_language(language: str) -> str:
    language = (language or "RU").strip()
    if "(" in language:
        language = language.split("(")[-1].split(")")[0].strip()
    return language.upper()


def translate_with_openai(text: str, target_language: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "Translate the user's text. Return only the translation."},
            {"role": "user", "content": f"Target language: {target_language}\n\n{text}"},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def translate_segments(segments: list[dict[str, Any]], target_language: str, project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    language = normalize_language(target_language)
    auth_key = os.getenv("DEEPL_AUTH_KEY")
    translated = []

    translator = None
    if auth_key:
        import deepl

        translator = deepl.Translator(auth_key)

    for index, segment in enumerate(segments):
        text = segment["text"]
        logger.info("Translating segment %s/%s", index + 1, len(segments))
        if translator:
            try:
                result = translator.translate_text(text, target_lang=language)
                translated_text = result.text
            except Exception as exc:
                logger.warning("DeepL translation failed, falling back to OpenAI: %s", exc)
                translated_text = translate_with_openai(text, language)
        else:
            translated_text = translate_with_openai(text, language)
        item = dict(segment)
        item["translated_text"] = translated_text
        item["voiceover_text"] = translated_text
        translated.append(item)
        time.sleep(float(os.getenv("DEEPL_DELAY", "0.2")))

    write_json(project / "work" / "source.translated.json", translated)
    translated_text_file = "\n".join(item["translated_text"] for item in translated)
    (project / "output" / "source.translated.txt").write_text(translated_text_file, encoding="utf-8")
    return translated


def improve_segments(segments: list[dict[str, Any]], params: dict[str, Any], project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    instructions = params.get("custom_instructions", "")
    if not instructions:
        write_json(project / "work" / "source.improved.json", segments)
        return segments

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    improved = []
    for index, segment in enumerate(segments):
        logger.info("Improving segment %s/%s", index + 1, len(segments))
        prompt = (
            "Improve this translated voiceover line while preserving meaning and language. "
            "Return only the improved line.\n\n"
            f"Original: {segment['text']}\n"
            f"Translation: {segment['translated_text']}\n"
            f"Instructions: {instructions}"
        )
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_IMPROVE_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        item = dict(segment)
        item["voiceover_text"] = response.choices[0].message.content.strip()
        improved.append(item)

    write_json(project / "work" / "source.improved.json", improved)
    return improved


def synthesize_segments(segments: list[dict[str, Any]], params: dict[str, Any], project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key)

    tts_dir = project / "work" / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    voice = params.get("voice") or os.getenv("OPENAI_TTS_VOICE", "alloy")

    synthesized = []
    for index, segment in enumerate(segments):
        text = str(segment.get("voiceover_text") or segment.get("translated_text") or "").strip()
        if not text:
            continue
        timing_format = get_timing_format(segment_end_seconds(segment))
        start = str(segment.get("start", seconds_to_timecode(segment_start_seconds(segment), timing_format)))
        end = str(segment.get("end", seconds_to_timecode(segment_end_seconds(segment), timing_format)))
        output = tts_dir / f"{start}-{end}.mp3"
        logger.info("Synthesizing segment %s/%s with voice %s", index + 1, len(segments), voice)
        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            response_format="mp3",
        ) as response:
            response.stream_to_file(output)
        item = dict(segment)
        item["tts_path"] = str(output)
        synthesized.append(item)
        time.sleep(float(os.getenv("OPENAI_TTS_DELAY", "0.2")))

    return synthesized


def mix_voiceover(source_mp3: Path, segments: list[dict[str, Any]], project: Path, logger: logging.Logger) -> Path:
    output_dir = project / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    background = AudioSegment.from_file(source_mp3) - float(os.getenv("VOICEOVER_BACKGROUND_REDUCTION_DB", "18"))
    narration_only = AudioSegment.silent(duration=0)
    mixed = AudioSegment.silent(duration=0)

    for segment in segments:
        tts_path = segment.get("tts_path")
        if not tts_path:
            continue
        clip = AudioSegment.from_file(tts_path)
        end_ms = max(0, int(segment_end_seconds(segment) * 1000))
        target_start_ms = max(0, end_ms - len(clip))

        # Match the legacy voiceover assembly: place clips as close as possible
        # to their segment end, but append immediately if that would overlap.
        current_ms = len(mixed)
        if target_start_ms > current_ms:
            gap = background[current_ms:target_start_ms]
            mixed += gap
            narration_only += AudioSegment.silent(duration=len(gap))
        elif target_start_ms < current_ms:
            logger.info(
                "Adjusted overlapping voiceover chunk %s-%s from %sms to %sms",
                segment.get("start"),
                segment.get("end"),
                target_start_ms,
                current_ms,
            )

        mixed += clip
        narration_only += clip

    if len(mixed) < len(background):
        mixed += background[len(mixed):]
        narration_only += AudioSegment.silent(duration=len(background) - len(narration_only))

    narration_path = output_dir / "source.russian-narration.mp3"
    voiceover_path = output_dir / "source.voiceover.mp3"
    narration_only.export(narration_path, format="mp3", bitrate="192k")
    mixed.export(voiceover_path, format="mp3", bitrate="192k")
    logger.info("Voiceover written to %s", voiceover_path)
    return voiceover_path


def run_stage(project: Path, manifest: dict[str, Any], name: str, fn, logger: logging.Logger):
    started_at = now_iso()
    try:
        result = fn()
    except Exception as exc:
        ended_at = now_iso()
        append_stage(manifest, name, "failed", started_at, ended_at, str(exc))
        write_json(project / "manifest.json", manifest)
        logger.exception("Stage failed: %s", name)
        raise
    ended_at = now_iso()
    append_stage(manifest, name, "completed", started_at, ended_at)
    write_json(project / "manifest.json", manifest)
    return result


def process_project(project: Path) -> None:
    logger = setup_logger(project)
    metadata = read_json(project / "metadata.json", {})
    params = read_json(project / "config" / "params.json", {})
    manifest = {
        "project_id": project.name,
        "worker_version": WORKER_VERSION,
        "created_at": now_iso(),
        "provider_selection": {
            "transcription": "openai",
            "translation": "deepl" if os.getenv("DEEPL_AUTH_KEY") else "openai",
            "tts": "openai",
        },
        "stages": [],
        "artifacts": [],
    }
    write_json(project / "manifest.json", manifest)
    update_status(project, "running", "starting", 2, "Worker started")
    logger.info("Processing %s", project.name)

    try:
        source = get_source(project, metadata)
        work_dir = project / "work"
        stages = set(str(params.get("stages_to_run", "transcribe+translate+voiceover")).split("+"))

        update_status(project, "running", "preprocess", 8, "Preparing source audio")
        source_mp3 = run_stage(project, manifest, "preprocess", lambda: ensure_mp3(source, work_dir, logger), logger)

        update_status(project, "running", "transcribe", 20, "Transcribing source audio")
        segments = run_stage(project, manifest, "transcribe", lambda: transcribe(source_mp3, project, logger), logger)

        if "translate" in stages or "voiceover" in stages or "improve" in stages:
            update_status(project, "running", "translate", 45, "Translating transcript")
            segments = run_stage(
                project,
                manifest,
                "translate",
                lambda: translate_segments(segments, params.get("target_language") or params.get("language") or "RU", project, logger),
                logger,
            )

        if "translate" in stages or "voiceover" in stages or "improve" in stages:
            update_status(project, "running", "improve", 62, "Preparing improved transcript")
            segments = run_stage(project, manifest, "improve", lambda: improve_segments(segments, params, project, logger), logger)

        if "voiceover" in stages:
            update_status(project, "running", "voiceover", 75, "Generating voiceover audio")
            synthesized = run_stage(project, manifest, "tts", lambda: synthesize_segments(segments, params, project, logger), logger)
            voiceover_path = run_stage(project, manifest, "mix", lambda: mix_voiceover(source_mp3, synthesized, project, logger), logger)
            manifest["artifacts"].append(
                {
                    "name": voiceover_path.name,
                    "path": f"output/{voiceover_path.name}",
                    "bytes": voiceover_path.stat().st_size,
                }
            )

        for artifact in (project / "output").glob("*"):
            if artifact.is_file():
                item = {"name": artifact.name, "path": f"output/{artifact.name}", "bytes": artifact.stat().st_size}
                if not any(existing.get("path") == item["path"] for existing in manifest["artifacts"]):
                    manifest["artifacts"].append(item)
        improved_artifact = project / "work" / "source.improved.json"
        if improved_artifact.exists():
            manifest["artifacts"].append(
                {
                    "name": improved_artifact.name,
                    "path": "work/source.improved.json",
                    "bytes": improved_artifact.stat().st_size,
                }
            )

        manifest["completed_at"] = now_iso()
        write_json(project / "manifest.json", manifest)
        update_status(project, "completed", "completed", 100, "Project completed")
        logger.info("Project completed")
    except Exception as exc:
        manifest["failed_at"] = now_iso()
        manifest["error"] = str(exc)
        write_json(project / "manifest.json", manifest)
        update_status(project, "failed", "failed", 100, "Project failed", error=str(exc))
        logger.exception("Project failed")
