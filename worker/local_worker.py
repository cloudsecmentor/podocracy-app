from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from pydub import AudioSegment, silence


WORKER_VERSION = "local-worker-0.3.0"
DEFAULT_SPEEDUP_VALUE = 1.2
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
CUSTOM_RECORDING_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".webm"}


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


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def read_url_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"URL file is empty: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError(f"URL file does not contain a url field: {path}")
    return url


def download_url_source(source: Path, project: Path, logger: logging.Logger) -> Path:
    url = read_url_file(source)
    download_dir = project / "work" / "url-download"
    download_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading source URL: %s", url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required for URL source processing") from exc

    options = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(download_dir / "source.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])

    candidates = [item for item in download_dir.iterdir() if item.is_file()]
    if not candidates:
        raise FileNotFoundError(f"No media file downloaded from {url}")
    candidates.sort(key=lambda item: item.stat().st_size, reverse=True)
    logger.info("Downloaded URL source to %s", candidates[0])
    return candidates[0]


def resolve_source_media(source: Path, project: Path, logger: logging.Logger) -> Path:
    if source.suffix.lower() == ".url":
        return download_url_source(source, project, logger)
    return source


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


def param_int(params: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(params.get(key, default))
    except (TypeError, ValueError):
        return default


def param_float(params: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return default


def split_audio_ranges(audio: AudioSegment, params: dict[str, Any], logger: logging.Logger) -> list[tuple[int, int]]:
    chunk_length_sec = param_int(params, "whisper_chunk_length_sec", 300)
    silence_sec = param_float(params, "whisper_silence_sec", 2.0)
    max_chunk_ms = max(1000, chunk_length_sec * 1000)
    min_chunk_ms = max(500, int(0.5 * max_chunk_ms))
    use_silence = parse_legacy_bool(params.get("whisper_silence_split", False))

    if len(audio) <= max_chunk_ms and not use_silence:
        return [(0, len(audio))]

    if not use_silence:
        return [(start, min(start + max_chunk_ms, len(audio))) for start in range(0, len(audio), max_chunk_ms)]

    logger.info("Splitting transcription audio on silence: chunk=%ss silence=%ss", chunk_length_sec, silence_sec)
    silence_segments = silence.detect_silence(audio, min_silence_len=int(silence_sec * 1000), silence_thresh=-40)
    if not silence_segments:
        return [(start, min(start + max_chunk_ms, len(audio))) for start in range(0, len(audio), max_chunk_ms)]

    ranges: list[tuple[int, int]] = []
    current_position = 0
    for start, end in silence_segments:
        middle = (start + end) // 2
        chunk_length = middle - current_position
        if min_chunk_ms <= chunk_length <= max_chunk_ms:
            ranges.append((current_position, middle))
            current_position = middle
        elif chunk_length > max_chunk_ms:
            while current_position + max_chunk_ms < middle:
                next_position = current_position + max_chunk_ms
                ranges.append((current_position, next_position))
                current_position = next_position
            if current_position < middle:
                ranges.append((current_position, middle))
                current_position = middle

    while current_position + max_chunk_ms < len(audio):
        next_position = current_position + max_chunk_ms
        ranges.append((current_position, next_position))
        current_position = next_position
    if current_position < len(audio):
        ranges.append((current_position, len(audio)))
    return ranges or [(0, len(audio))]


def collect_word_entries(data: dict[str, Any], offset_seconds: float = 0.0) -> list[dict[str, Any]]:
    raw_words: list[dict[str, Any]] = []
    if data.get("words"):
        raw_words.extend(data.get("words") or [])
    for segment in data.get("segments") or []:
        raw_words.extend(segment.get("words") or [])

    words: list[dict[str, Any]] = []
    for entry in raw_words:
        word = str(entry.get("word") or "").strip()
        if not word:
            continue
        start = float(entry.get("start", entry.get("end", 0.0))) + offset_seconds
        end = float(entry.get("end", entry.get("start", start))) + offset_seconds
        words.append({"word": word, "start": start, "end": end})
    return words


def words_from_segments(data: dict[str, Any], offset_seconds: float = 0.0) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in data.get("segments") or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start", 0.0)) + offset_seconds
        end = float(segment.get("end", start)) + offset_seconds
        parts = [part for part in text.split() if part]
        duration = max(0.0, end - start)
        step = duration / len(parts) if parts else 0
        for index, word in enumerate(parts):
            words.append(
                {
                    "word": word,
                    "start": start + index * step,
                    "end": start + (index + 1) * step if index < len(parts) - 1 else end,
                }
            )
    return words


def combine_words_to_sentences_local(words: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    max_chars = param_int(params, "max_char_chunk_per_sentence", 200)
    pause_sec = param_float(params, "delay_between_words_for_new_sentence_chunk", 2.0)
    sentences: list[dict[str, Any]] = []
    current = ""
    start_time = 0.0
    end_time = 0.0

    for index, word_obj in enumerate(words):
        word = str(word_obj.get("word") or "").strip()
        if not word:
            continue
        if not current:
            start_time = float(word_obj["start"])
        end_time = float(word_obj.get("end", word_obj["start"]))

        if word in {".", "!", "?", ",", ";", ":"}:
            current = current.rstrip() + word
            if word in {",", ";", ":"}:
                current += " "
        elif re.match(r"^[,.;:!?]+$", word):
            current = current.rstrip() + word
        else:
            current += word + " "

        next_pause = 0.0
        if index + 1 < len(words):
            next_pause = float(words[index + 1].get("start", end_time)) - end_time
        sentence = {"start_seconds": start_time, "end_seconds": end_time, "text": current.strip()}
        if word.endswith((".", "!", "?")) or len(current) >= max_chars or next_pause > pause_sec:
            sentences.append(sentence)
            current = ""

    if current:
        sentences.append({"start_seconds": start_time, "end_seconds": end_time, "text": current.strip()})
    return sentences


def combine_sentences_to_chunks_local(sentences: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    max_chars = param_int(params, "max_char_chunk", 700)
    pause_sec = param_float(params, "delay_between_words_for_new_sentence_chunk", 2.0)
    max_end = max([float(sentence["end_seconds"]) for sentence in sentences] or [0.0])
    timing_format = get_timing_format(max_end)
    chunks: list[dict[str, Any]] = []
    current = ""
    chunk_start = 0.0
    chunk_end = 0.0

    for sentence in sentences:
        text = str(sentence.get("text") or "").strip()
        if not text:
            continue
        sentence_start = float(sentence["start_seconds"])
        sentence_end = float(sentence["end_seconds"])
        if not current:
            current = text + " "
            chunk_start = sentence_start
            chunk_end = sentence_end
            continue
        if len(current + text) > max_chars or sentence_start - chunk_end > pause_sec:
            chunks.append(
                {
                    "id": len(chunks),
                    "start": seconds_to_timecode(chunk_start, timing_format),
                    "end": seconds_to_timecode(chunk_end, timing_format),
                    "start_seconds": chunk_start,
                    "end_seconds": chunk_end,
                    "text": current.strip(),
                }
            )
            current = text + " "
            chunk_start = sentence_start
            chunk_end = sentence_end
        else:
            current += text + " "
            chunk_end = sentence_end

    if current:
        chunks.append(
            {
                "id": len(chunks),
                "start": seconds_to_timecode(chunk_start, timing_format),
                "end": seconds_to_timecode(chunk_end, timing_format),
                "start_seconds": chunk_start,
                "end_seconds": chunk_end,
                "text": current.strip(),
            }
        )
    return chunks


def transcribe(source_mp3: Path, params: dict[str, Any], project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
    detailed = parse_legacy_bool(params.get("detailed_transcription", True))
    logger.info("Transcribing with OpenAI model %s detailed=%s", model, detailed)

    audio = AudioSegment.from_file(source_mp3)
    ranges = split_audio_ranges(audio, params, logger)
    chunk_dir = project / "work" / "transcription-chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    raw_chunks: list[dict[str, Any]] = []
    all_words: list[dict[str, Any]] = []
    all_segments: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for index, (start_ms, end_ms) in enumerate(ranges):
        chunk_path = chunk_dir / f"chunk-{index:04d}-{start_ms}-{end_ms}.mp3"
        audio[start_ms:end_ms].export(chunk_path, format="mp3", bitrate="192k")
        with chunk_path.open("rb") as audio_file:
            try:
                transcript = client.audio.transcriptions.create(
                    model=model,
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["word"] if detailed else ["segment"],
                )
            except TypeError:
                audio_file.seek(0)
                transcript = client.audio.transcriptions.create(
                    model=model,
                    file=audio_file,
                    response_format="verbose_json",
                )

        offset_seconds = start_ms / 1000.0
        data = model_dump(transcript)
        text_parts.append(str(data.get("text") or "").strip())
        raw_chunks.append({"start_ms": start_ms, "end_ms": end_ms, "transcript": data})
        words = collect_word_entries(data, offset_seconds) if detailed else []
        if not words:
            words = words_from_segments(data, offset_seconds)
        all_words.extend(words)

        for segment in data.get("segments") or []:
            item = dict(segment)
            item["start"] = float(item.get("start", 0.0)) + offset_seconds
            item["end"] = float(item.get("end", item["start"])) + offset_seconds
            all_segments.append(item)
        logger.info("Transcribed chunk %s/%s with %s words", index + 1, len(ranges), len(words))

    all_words.sort(key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
    raw = {
        "text": " ".join(part for part in text_parts if part).strip(),
        "segments": [{"words": all_words}],
        "raw_segments": all_segments,
        "chunks": raw_chunks,
    }
    write_json(project / "work" / "source.raw.json", raw)

    if all_words:
        sentences = combine_words_to_sentences_local(all_words, params)
        cleaned = combine_sentences_to_chunks_local(sentences, params)
    else:
        cleaned = []

    if not cleaned and raw["text"]:
        duration = audio.duration_seconds
        timing_format = get_timing_format(duration)
        cleaned = [
            {
                "id": 0,
                "start": seconds_to_timecode(0, timing_format),
                "end": seconds_to_timecode(duration, timing_format),
                "start_seconds": 0.0,
                "end_seconds": duration,
                "text": raw["text"],
            }
        ]

    write_json(project / "work" / "source.combined.json", cleaned)
    (project / "output" / "source.transcript.txt").write_text(raw["text"], encoding="utf-8")
    return cleaned


def parse_timestamp_to_seconds(value: str) -> float:
    text = value.strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported subtitle timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt_vtt_subtitles(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.upper().startswith("WEBVTT"):
            index += 1
            continue
        if line.isdigit():
            index += 1
            if index >= len(lines):
                break
            line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        start_text, end_text = line.split("-->", 1)
        start_seconds = parse_timestamp_to_seconds(start_text.strip())
        end_seconds = parse_timestamp_to_seconds(end_text.strip().split()[0])
        index += 1
        text_lines: list[str] = []
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line:
                index += 1
                if text_lines:
                    break
                continue
            if next_line.isdigit() or "-->" in next_line:
                break
            text_lines.append(next_line)
            index += 1
        text = " ".join(text_lines).strip()
        if text:
            entries.append({"start_seconds": start_seconds, "end_seconds": end_seconds, "text": text})
    return entries


def parse_sbv_subtitles(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    timing = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*,\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*$")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not timing.match(line):
            index += 1
            continue
        start_text, end_text = line.split(",", 1)
        start_seconds = parse_timestamp_to_seconds(start_text)
        end_seconds = parse_timestamp_to_seconds(end_text)
        index += 1
        text_lines: list[str] = []
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line:
                index += 1
                if text_lines:
                    break
                continue
            if timing.match(next_line):
                break
            text_lines.append(next_line)
            index += 1
        text = " ".join(text_lines).strip()
        if text:
            entries.append({"start_seconds": start_seconds, "end_seconds": end_seconds, "text": text})
    return entries


def parse_subtitles(path: Path, params: dict[str, Any], project: Path, use_as_is: bool, logger: logging.Logger) -> list[dict[str, Any]]:
    extension = path.suffix.lower().lstrip(".")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if extension in {"srt", "vtt"} or any("-->" in line for line in lines):
        raw_entries = parse_srt_vtt_subtitles(lines)
    elif extension in {"sbv", "txt"}:
        raw_entries = parse_sbv_subtitles(lines)
    else:
        raise ValueError(f"Unsupported subtitle format: {path.suffix}")
    if not raw_entries:
        raise ValueError(f"No subtitle entries parsed from {path}")

    timing_format = get_timing_format(max(float(item["end_seconds"]) for item in raw_entries))
    translation_key = str(params.get("translation_text_key") or "translated_text")
    improved_key = str(params.get("improved_text_key") or "voiceover_text")
    segments: list[dict[str, Any]] = []
    for index, item in enumerate(raw_entries):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start_seconds = float(item["start_seconds"])
        end_seconds = float(item["end_seconds"])
        segment = {
            "id": index,
            "start": seconds_to_timecode(start_seconds, timing_format),
            "end": seconds_to_timecode(end_seconds, timing_format),
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "text": text,
        }
        if use_as_is:
            segment[translation_key] = text
            segment[improved_key] = text
        segments.append(segment)

    write_json(project / "work" / "source.raw.json", {"source": str(path), "segments": raw_entries})
    write_json(project / "work" / "source.combined.json", segments)
    (project / "output" / "source.transcript.txt").write_text("\n".join(item["text"] for item in segments), encoding="utf-8")
    logger.info("Loaded %s subtitle segments from %s", len(segments), path)
    return segments


def get_subtitle_path(project: Path, params: dict[str, Any]) -> Path | None:
    relative = str(params.get("custom_subtitles") or "").strip()
    if not relative:
        return None
    path = project / relative
    return path if path.exists() else None


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


def translate_segments(
    segments: list[dict[str, Any]],
    target_language: str,
    params: dict[str, Any],
    project: Path,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    language = normalize_language(target_language)
    auth_key = os.getenv("DEEPL_AUTH_KEY")
    translation_key = str(params.get("translation_text_key") or "translated_text")
    improved_key = str(params.get("improved_text_key") or "voiceover_text")
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
        item[translation_key] = translated_text
        item[improved_key] = translated_text
        translated.append(item)
        time.sleep(float(os.getenv("DEEPL_DELAY", "0.2")))

    write_json(project / "work" / "source.translated.json", translated)
    translated_text_file = "\n".join(item[translation_key] for item in translated)
    (project / "output" / "source.translated.txt").write_text(translated_text_file, encoding="utf-8")
    return translated


def improve_segments(segments: list[dict[str, Any]], params: dict[str, Any], project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    translation_key = str(params.get("translation_text_key") or "translated_text")
    improved_key = str(params.get("improved_text_key") or "voiceover_text")
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_IMPROVE_MODEL", str(params.get("improve_openai_model") or "gpt-5"))
    instructions = str(params.get("custom_instructions") or "").strip()
    max_chars = param_int(params, "improve_max_chunk_chars", 12000)
    max_items = param_int(params, "improve_max_chunk_items", 90)
    sleep_time = param_float(params, "sleep_time_improve", 0.5)

    megachunks: list[list[tuple[int, dict[str, Any]]]] = []
    current: list[tuple[int, dict[str, Any]]] = []
    current_length = 0
    for index, segment in enumerate(segments):
        text = str(segment.get(translation_key) or "").strip()
        if not text:
            continue
        text_length = len(text)
        if current and (current_length + text_length > max_chars or len(current) >= max_items):
            megachunks.append(current)
            current = []
            current_length = 0
        current.append((index, segment))
        current_length += text_length
    if current:
        megachunks.append(current)

    improved = [dict(segment) for segment in segments]
    for megachunk_index, megachunk in enumerate(megachunks):
        translated_payload: dict[str, str] = {}
        original_payload: dict[str, str] = {}
        key_to_index: dict[str, int] = {}
        for index, segment in megachunk:
            key = f"chunk_{index:04d}"
            translated_payload[key] = str(segment.get(translation_key) or "")
            original_payload[key] = str(segment.get("text") or "")
            key_to_index[key] = index

        custom_instructions_text = f"\n- Pay special attention to these instructions:\n{instructions}\n" if instructions else ""
        prompt = f"""
You will receive two JSON dictionaries: translated texts to improve, and original texts for context.
Improve each translated text while using the full dictionary as context for terminology, tone, names, and continuity.

Texts to improve:
{json.dumps(translated_payload, ensure_ascii=False)}

Original texts:
{json.dumps(original_payload, ensure_ascii=False)}

Guidelines:
- Return only a JSON object with exactly the same keys as the input texts.
- Preserve meaning and do not embellish.
- Make phrasing natural, concise, and suitable for spoken voiceover.
- Convert numerals to words when that sounds natural in the target language.
- Fix grammar, awkward phrasing, and non-native patterns.
- Keep empty outputs as empty strings if a chunk is only filler after improvement.
{custom_instructions_text}
""".strip()
        kwargs: dict[str, Any] = {"response_format": {"type": "json_object"}}
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = param_float(params, "improve_openai_temperature", 0.3)
        logger.info("Improving megachunk %s/%s with %s chunks using %s", megachunk_index + 1, len(megachunks), len(megachunk), model)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a careful translation editor for spoken voiceover."},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )
        raw = response.choices[0].message.content or "{}"
        try:
            improved_payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Improve model returned invalid JSON: {raw[:500]}") from exc

        for key, original_index in key_to_index.items():
            improved[original_index][improved_key] = str(improved_payload.get(key, translated_payload[key])).strip()
        time.sleep(sleep_time)

    write_json(project / "work" / "source.improved.json", improved)
    return improved


def combine_segment_text(segments: list[dict[str, Any]], key: str) -> str:
    return " ".join(str(segment.get(key) or "").strip() for segment in segments).strip()


def customize_instructions(segments: list[dict[str, Any]], params: dict[str, Any], project: Path, logger: logging.Logger) -> dict[str, Any]:
    if not parse_legacy_bool(params.get("autogenerate_custom_instructions", False)):
        return params

    from openai import OpenAI

    original_text = combine_segment_text(segments, "text")
    translation_key = str(params.get("translation_text_key") or "translated_text")
    translated_text = combine_segment_text(segments, translation_key)
    existing = str(params.get("custom_instructions") or "").strip()
    number_of_issues = int(params.get("number_of_issues_to_find", 50))
    style_instruction = (
        f"\n\nThe user provided these instructions:\n{existing}\nGenerate additional concrete instructions in the same style."
        if existing
        else "\n\nFor each issue, provide a short, concrete instruction. Avoid broad generalities."
    )
    prompt = f"""
Analyze this translation and provide recommendations to make it more natural and culturally appropriate.

Original text:
{original_text}

Translation:
{translated_text}
{style_instruction}

Limit the list to no more than {number_of_issues} issues. Return only the instructions.
""".strip()

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_CUSTOMIZE_MODEL", str(params.get("openai_model") or "gpt-5"))
    logger.info("Autogenerating custom improvement instructions with %s", model)
    kwargs = {}
    if not model.startswith("gpt-5"):
        kwargs["temperature"] = 0
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    generated = response.choices[0].message.content.strip()
    updated = dict(params)
    updated["custom_instructions"] = f"{existing}\n--------------\n{generated}".strip()
    write_json(project / "work" / "source.custom-instructions.json", {"instructions": generated})
    write_json(project / "config" / "params.json", updated)
    return updated


def synthesize_openai_tts(text: str, output: Path, params: dict[str, Any]) -> None:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key)

    model = os.getenv("OPENAI_TTS_MODEL", str(params.get("openai_model_tts") or "gpt-4o-mini-tts"))
    voice = params.get("voice") or os.getenv("OPENAI_TTS_VOICE", "alloy")
    with client.audio.speech.with_streaming_response.create(
        model=model,
        voice=voice,
        input=text,
        response_format="mp3",
    ) as response:
        response.stream_to_file(output)


def synthesize_elevenlabs_tts(text: str, output: Path, params: dict[str, Any]) -> None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    voice_id = str(params.get("elevenlabs_voice_id") or os.getenv("ELEVENLABS_VOICE_ID") or "iP95p4xoKVk53GoZ742B")
    model_id = str(params.get("elevenlabs_model_id") or os.getenv("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2")
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "accept": "audio/mpeg",
            "content-type": "application/json",
        },
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": float(params.get("elevenlabs_stability", 0.9)),
                "similarity_boost": float(params.get("elevenlabs_similarity_boost", 1.0)),
                "style": float(params.get("elevenlabs_style", 0.0)),
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    output.write_bytes(response.content)


def synthesize_segments(segments: list[dict[str, Any]], params: dict[str, Any], project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    tts_dir = project / "work" / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    tts_api = str(params.get("tts_api") or "openai").lower()
    voice = params.get("voice") or os.getenv("OPENAI_TTS_VOICE", "alloy")
    sleep_time = float(params.get("sleep_time_tts", os.getenv("OPENAI_TTS_DELAY", "0.2")))
    translation_key = str(params.get("translation_text_key") or "translated_text")
    improved_key = str(params.get("improved_text_key") or "voiceover_text")

    synthesized = []
    for index, segment in enumerate(segments):
        if segment.get("tts_path"):
            synthesized.append(segment)
            continue
        text = str(segment.get(improved_key) or segment.get(translation_key) or "").strip()
        if not text:
            continue
        timing_format = get_timing_format(segment_end_seconds(segment))
        start = str(segment.get("start", seconds_to_timecode(segment_start_seconds(segment), timing_format)))
        end = str(segment.get("end", seconds_to_timecode(segment_end_seconds(segment), timing_format)))
        output = tts_dir / f"{start}-{end}.mp3"
        if tts_api == "elevenlabs":
            logger.info("Synthesizing segment %s/%s with ElevenLabs", index + 1, len(segments))
            synthesize_elevenlabs_tts(text, output, params)
        else:
            logger.info("Synthesizing segment %s/%s with OpenAI voice %s", index + 1, len(segments), voice)
            synthesize_openai_tts(text, output, params)
        item = dict(segment)
        item["tts_path"] = str(output)
        synthesized.append(item)
        time.sleep(sleep_time)

    return synthesized


def parse_legacy_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def legacy_default_speedup() -> float:
    params_path = Path(__file__).resolve().parent / "processing_container" / "parameters.json"
    if not params_path.exists():
        return DEFAULT_SPEEDUP_VALUE
    data = read_json(params_path, {})
    entry = data.get("speedup_value", DEFAULT_SPEEDUP_VALUE)
    if isinstance(entry, dict):
        return float(entry.get("value", DEFAULT_SPEEDUP_VALUE))
    return float(entry)


def resolve_voiceover_tempo(params: dict[str, Any], logger: logging.Logger) -> float:
    if parse_legacy_bool(params.get("custom_recording", False)):
        logger.info("custom_recording enabled; voiceover tempo forced to 1.0")
        return 1.0

    voiceover_tempo = params.get("voiceover_tempo")
    if voiceover_tempo is not None:
        try:
            return float(voiceover_tempo)
        except (TypeError, ValueError):
            logger.warning("Invalid voiceover_tempo value %r, using default", voiceover_tempo)

    speedup_value = params.get("speedup_value")
    if speedup_value is not None:
        try:
            return float(speedup_value)
        except (TypeError, ValueError):
            logger.warning("Invalid speedup_value %r, using default", speedup_value)

    return legacy_default_speedup()


def apply_voiceover_tempo(
    segments: list[dict[str, Any]],
    tempo: float,
    project: Path,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    if tempo == 1.0:
        return segments

    logger.info(
        "Changing voiceover speed by %s%%. Consider 'Change Tempo' in Audacity for better quality.",
        round((tempo - 1) * 100),
    )
    adjusted_dir = project / "work" / "tts-adjusted"
    adjusted_dir.mkdir(parents=True, exist_ok=True)

    adjusted: list[dict[str, Any]] = []
    for segment in segments:
        tts_path = segment.get("tts_path")
        if not tts_path:
            adjusted.append(segment)
            continue

        infile = Path(tts_path)
        outfile = adjusted_dir / infile.name
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(infile),
            "-filter:a",
            f"atempo={tempo}",
            str(outfile),
        ]
        logger.info("Applying tempo %s to %s", tempo, infile.name)
        subprocess.run(command, check=True)
        item = dict(segment)
        item["tts_path"] = str(outfile)
        adjusted.append(item)
    return adjusted


def segment_key(segment: dict[str, Any]) -> str:
    timing_format = get_timing_format(segment_end_seconds(segment))
    start = str(segment.get("start", seconds_to_timecode(segment_start_seconds(segment), timing_format)))
    end = str(segment.get("end", seconds_to_timecode(segment_end_seconds(segment), timing_format)))
    return f"{start}-{end}"


def normalize_stem(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z-]+", "-", value).strip("-").lower()


def convert_custom_recording(infile: Path, outfile: Path, logger: logging.Logger) -> None:
    filters = "silenceremove=stop_periods=-1:stop_duration=0.2:stop_threshold=-40dB"
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(infile),
        "-af",
        filters,
        "-acodec",
        "libmp3lame",
        "-ab",
        "192k",
        str(outfile),
    ]
    logger.info("Converting custom recording %s", infile.name)
    subprocess.run(command, check=True)


def extract_zip_safely(archive_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise ValueError(f"Unsafe path in custom recordings archive: {member.filename}")
        archive.extractall(destination)


def load_custom_recordings(segments: list[dict[str, Any]], params: dict[str, Any], project: Path, logger: logging.Logger) -> list[dict[str, Any]]:
    relative = str(params.get("custom_recordings_zip") or "").strip()
    if not relative:
        raise FileNotFoundError("custom_recording is enabled but custom_recordings_zip is missing")
    archive_path = project / relative
    if not archive_path.exists():
        raise FileNotFoundError(f"Custom recordings archive not found: {archive_path}")

    raw_dir = project / "work" / "custom-recordings-raw"
    processed_dir = project / "work" / "custom-recordings"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    extract_zip_safely(archive_path, raw_dir)

    recordings: dict[str, Path] = {}
    sorted_recordings: list[Path] = []
    for infile in sorted(raw_dir.rglob("*")):
        if not infile.is_file() or infile.suffix.lower() not in CUSTOM_RECORDING_EXTENSIONS:
            continue
        outfile = processed_dir / f"{normalize_stem(infile.stem)}.mp3"
        convert_custom_recording(infile, outfile, logger)
        recordings[normalize_stem(infile.stem)] = outfile
        sorted_recordings.append(outfile)

    if not sorted_recordings:
        raise FileNotFoundError(f"No supported recordings found in {archive_path}")

    synthesized: list[dict[str, Any]] = []
    missing: list[str] = []
    for index, segment in enumerate(segments):
        candidates = [
            normalize_stem(segment_key(segment)),
            normalize_stem(str(segment.get("start", ""))),
            f"{index:03d}",
            str(index),
        ]
        recording = next((recordings[key] for key in candidates if key in recordings), None)
        if recording is None and index < len(sorted_recordings):
            recording = sorted_recordings[index]
        if recording is None:
            missing.append(segment_key(segment))
            continue
        item = dict(segment)
        item["tts_path"] = str(recording)
        item["custom_recording"] = True
        synthesized.append(item)

    if missing:
        raise FileNotFoundError(f"Missing custom recordings for segments: {', '.join(missing[:8])}")
    logger.info("Loaded %s custom recordings", len(synthesized))
    return synthesized


def resolve_voiceover_shift(params: dict[str, Any], logger: logging.Logger) -> float:
    value = params.get("voiceover_shift", 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        logger.warning("Invalid voiceover_shift value %r, using 0", value)
        return 0.0


def maybe_normalize_audio(path: Path, params: dict[str, Any], logger: logging.Logger) -> Path:
    if not parse_legacy_bool(params.get("normalize_final_audio", False)):
        return path
    target = float(params.get("normalize_target_i", os.getenv("VOICEOVER_NORMALIZE_TARGET_I", "-16")))
    limiter = float(params.get("normalize_limiter", os.getenv("VOICEOVER_LIMITER_DB", "-3.5")))
    normalized = path.with_name(f"{path.stem}.normalized{path.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-af",
        f"loudnorm=I={target}:TP={limiter}:LRA=11,alimiter=limit={limiter}dB",
        str(normalized),
    ]
    logger.info("Normalizing final audio to %s", normalized)
    subprocess.run(command, check=True)
    shutil.move(normalized, path)
    return path


def mix_voiceover(
    source_mp3: Path,
    segments: list[dict[str, Any]],
    project: Path,
    logger: logging.Logger,
    shift_seconds: float = 0.0,
) -> Path:
    output_dir = project / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    background = AudioSegment.from_file(source_mp3) - float(os.getenv("VOICEOVER_BACKGROUND_REDUCTION_DB", "18"))
    narration_only = AudioSegment.silent(duration=0)
    mixed = AudioSegment.silent(duration=0)
    shift_ms = int(shift_seconds * 1000)

    for segment in segments:
        tts_path = segment.get("tts_path")
        if not tts_path:
            continue
        clip = AudioSegment.from_file(tts_path)
        end_ms = max(0, int(segment_end_seconds(segment) * 1000))
        target_start_ms = max(0, end_ms - len(clip) + shift_ms)

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


def create_video_artifacts(source_media: Path, voiceover_path: Path, params: dict[str, Any], project: Path, logger: logging.Logger) -> list[Path]:
    if not is_video_file(source_media):
        return []
    output_dir = project / "output"
    video_path = output_dir / "source.voiceover.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_media),
        "-i",
        str(voiceover_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(video_path),
    ]
    logger.info("Merging voiceover audio into video")
    subprocess.run(command, check=True)

    preview_path = output_dir / "source.voiceover_preview.mp4"
    max_mb = float(params.get("max_preview_size_mb", 2.0))
    size_mb = video_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_mb:
        shutil.copy2(video_path, preview_path)
    else:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-t",
                str(float(params.get("preview_length_seconds", 120))),
                "-vf",
                "scale=1280:-2",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:v",
                "900k",
                str(preview_path),
            ],
            check=True,
        )
    return [video_path, preview_path]


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
        source_media = resolve_source_media(source, project, logger)
        work_dir = project / "work"
        stages = set(str(params.get("stages_to_run", "transcribe+translate+voiceover")).split("+"))
        use_subtitles_as_is = parse_legacy_bool(params.get("use_subtitles_as_is", False))

        update_status(project, "running", "preprocess", 8, "Preparing source audio")
        source_mp3 = run_stage(project, manifest, "preprocess", lambda: ensure_mp3(source_media, work_dir, logger), logger)

        subtitle_path = get_subtitle_path(project, params)
        if subtitle_path:
            update_status(project, "running", "subtitles", 20, "Loading subtitles")
            segments = run_stage(project, manifest, "subtitles", lambda: parse_subtitles(subtitle_path, params, project, use_subtitles_as_is, logger), logger)
        else:
            update_status(project, "running", "transcribe", 20, "Transcribing source audio")
            segments = run_stage(project, manifest, "transcribe", lambda: transcribe(source_mp3, params, project, logger), logger)

        if not use_subtitles_as_is and ("translate" in stages or "voiceover" in stages or "improve" in stages):
            update_status(project, "running", "translate", 45, "Translating transcript")
            segments = run_stage(
                project,
                manifest,
                "translate",
                lambda: translate_segments(segments, params.get("target_language") or params.get("language") or "RU", params, project, logger),
                logger,
            )

        if not use_subtitles_as_is and parse_legacy_bool(params.get("autogenerate_custom_instructions", False)):
            update_status(project, "running", "customize", 56, "Generating custom improvement instructions")
            params = run_stage(project, manifest, "customize", lambda: customize_instructions(segments, params, project, logger), logger)

        if not use_subtitles_as_is and ("voiceover" in stages or "improve" in stages):
            update_status(project, "running", "improve", 62, "Preparing improved transcript")
            segments = run_stage(project, manifest, "improve", lambda: improve_segments(segments, params, project, logger), logger)

        if "voiceover" in stages:
            voiceover_tempo = resolve_voiceover_tempo(params, logger)
            voiceover_shift = resolve_voiceover_shift(params, logger)
            manifest["voiceover_tempo"] = voiceover_tempo
            manifest["voiceover_shift"] = voiceover_shift
            manifest["provider_selection"]["tts"] = str(params.get("tts_api") or "openai").lower()
            update_status(project, "running", "voiceover", 75, "Generating voiceover audio")
            if parse_legacy_bool(params.get("custom_recording", False)):
                synthesized = run_stage(project, manifest, "custom-recordings", lambda: load_custom_recordings(segments, params, project, logger), logger)
            else:
                synthesized = run_stage(project, manifest, "tts", lambda: synthesize_segments(segments, params, project, logger), logger)
            if voiceover_tempo != 1.0:
                synthesized = run_stage(
                    project,
                    manifest,
                    "tempo",
                    lambda: apply_voiceover_tempo(synthesized, voiceover_tempo, project, logger),
                    logger,
                )
            voiceover_path = run_stage(project, manifest, "mix", lambda: mix_voiceover(source_mp3, synthesized, project, logger, voiceover_shift), logger)
            voiceover_path = run_stage(project, manifest, "normalize", lambda: maybe_normalize_audio(voiceover_path, params, logger), logger)
            manifest["artifacts"].append(
                {
                    "name": voiceover_path.name,
                    "path": f"output/{voiceover_path.name}",
                    "bytes": voiceover_path.stat().st_size,
                }
            )
            video_artifacts = run_stage(project, manifest, "postprocess-video", lambda: create_video_artifacts(source_media, voiceover_path, params, project, logger), logger)
            for artifact in video_artifacts:
                manifest["artifacts"].append(
                    {
                        "name": artifact.name,
                        "path": f"output/{artifact.name}",
                        "bytes": artifact.stat().st_size,
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
