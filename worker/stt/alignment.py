"""Word/punctuation alignment for providers that return unpunctuated words.

The OpenAI transcription API returns punctuated text plus a separate word list
without punctuation. The combine stage splits sentences on punctuation, so the
punctuated tokens are re-timed against the word list. Ported from
``shared_functions.add_start_end_times_to_transcript`` (used by the previous
``pd-010-02-whisper-api-transcribe.py``) so timings keep matching exactly.
"""

from __future__ import annotations

import copy
import re
import string
from typing import Any

# How many windows ahead of the last match to search before giving up.
WINDOWS_TO_SEARCH = 10
MATCH_RATIO = 0.8


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"\b\w[\w'-]*\b|[^\w\s]", text or "")


def word_entry_get(word_entry: Any, key: str) -> Any:
    if isinstance(word_entry, dict):
        return word_entry.get(key)
    return getattr(word_entry, key, None)


def normalize_word_entries(words: list[Any]) -> list[dict[str, Any]]:
    return [
        word
        if isinstance(word, dict)
        else {
            "word": word_entry_get(word, "word"),
            "start": word_entry_get(word, "start"),
            "end": word_entry_get(word, "end"),
        }
        for word in words
    ]


def _comparable(word: Any) -> str:
    return str(word or "").lower().strip(string.punctuation)


def add_start_end_times_to_transcript(
    transcript_redacted_words: list[dict[str, Any]],
    transcript_raw_words: list[Any],
    window_size: int,
) -> list[dict[str, Any]]:
    """Copy start/end times from timed words onto punctuated tokens.

    Windows of ``window_size`` tokens are matched against the timed word list;
    when at least 80% of a window matches, the timings are copied across. Tokens
    that never match keep no timing and are filled in by the caller.
    """
    transcript_words = copy.deepcopy(transcript_redacted_words)
    raw_words = normalize_word_entries(transcript_raw_words)
    if window_size < 1 or not raw_words:
        return transcript_words

    def is_match(window1: list[dict[str, Any]], window2: list[dict[str, Any]]) -> bool:
        matching = sum(
            1
            for first, second in zip(window1, window2)
            if _comparable(first.get("word")) == _comparable(second.get("word"))
        )
        return matching >= MATCH_RATIO * window_size

    index = 0
    latest_match = 0
    while index < len(transcript_words) - window_size:
        window_transcript = transcript_words[index : index + window_size]
        window_limit = min(latest_match + window_size * WINDOWS_TO_SEARCH, len(raw_words) - window_size)
        for candidate in range(latest_match, window_limit):
            window_raw = raw_words[candidate : candidate + window_size]
            if not is_match(window_transcript, window_raw):
                continue
            latest_match = candidate
            for offset, _ in enumerate(window_transcript):
                target = transcript_words[index + offset]
                if "start" in target:
                    continue
                if _comparable(target.get("word")) == _comparable(window_raw[offset].get("word")):
                    target["start"] = window_raw[offset].get("start")
                    target["end"] = window_raw[offset].get("end")
            break
        index += max(1, int(window_size / 2))

    return transcript_words


def fill_missing_timings(words: list[dict[str, Any]], fallback_start: float = 0.0) -> list[dict[str, Any]]:
    """Give every word a numeric start/end, collapsing unmatched tokens onto the previous end."""
    filled: list[dict[str, Any]] = []
    previous_end = float(fallback_start)
    for entry in words:
        item = dict(entry)
        start = item.get("start")
        end = item.get("end")
        item["start"] = float(start) if start is not None else previous_end
        item["end"] = float(end) if end is not None else item["start"]
        if item["end"] < item["start"]:
            item["end"] = item["start"]
        previous_end = item["end"]
        filled.append(item)
    return filled
