from shared_functions import *
import os
import re


def build_number_conversion_instructions(path):
    try:
        language = get_params("language", path)
    except Exception:
        language = "EN"
    return "\n".join([
        f"- The target language is {language}. Use this language for the output.",
        "- Convert all numerals (digits) to words, choosing the correct grammatical form based on context.",
        "- Preserve meaning and style; avoid edits beyond what is needed for number conversion.",
    ])


def parse_timestamp_to_seconds(value):
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported timestamp format: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def is_srt_vtt_timing_line(line):
    return "-->" in line


def is_sbv_timing_line(line):
    sbv_pattern = re.compile(
        r"^\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*,\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*$"
    )
    return bool(sbv_pattern.match(line))


def parse_srt_vtt(lines):
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        if line.upper().startswith("WEBVTT"):
            i += 1
            continue

        if line.upper().startswith("NOTE"):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            continue

        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()

        if not is_srt_vtt_timing_line(line):
            i += 1
            continue

        start_str, end_str = line.split("-->", 1)
        start = parse_timestamp_to_seconds(start_str.strip())
        end_part = end_str.strip().split()[0]
        end = parse_timestamp_to_seconds(end_part)

        i += 1
        text_lines = []
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                i += 1
                continue
            if is_srt_vtt_timing_line(next_line) or next_line.isdigit():
                break
            if next_line.upper().startswith("NOTE"):
                break
            text_lines.append(next_line)
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            entries.append({"start": start, "end": end, "text": text})

    return entries


def parse_sbv(lines):
    entries = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        if not is_sbv_timing_line(line):
            i += 1
            continue

        start_str, end_str = line.split(",", 1)
        start = parse_timestamp_to_seconds(start_str.strip())
        end = parse_timestamp_to_seconds(end_str.strip())

        i += 1
        text_lines = []
        while i < len(lines):
            next_line = lines[i].strip()
            if not next_line:
                i += 1
                continue
            if is_sbv_timing_line(next_line):
                break
            text_lines.append(next_line)
            i += 1
        text = " ".join(text_lines).strip()
        if text:
            entries.append({"start": start, "end": end, "text": text})

    return entries


def parse_subtitles_file_custom_parser(local_path, extension):
    with open(local_path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()

    if extension in ("srt", "vtt"):
        return parse_srt_vtt(lines)
    if extension == "sbv":
        return parse_sbv(lines)
    if extension == "txt":
        detected = detect_txt_subtitle_format(lines)
        if detected == "srt_vtt":
            return parse_srt_vtt(lines)
        if detected == "sbv":
            return parse_sbv(lines)
        raise ValueError("Unsupported .txt subtitle format: no timestamps detected")

    raise ValueError(f"Unsupported subtitle format: {extension}")

# Cleaner alternative using only pysubs2
import pysubs2

def parse_subtitles_file(local_path, extension):
    if extension == "txt":
        return parse_subtitles_file_custom_parser(local_path, extension)
    subs = pysubs2.load(local_path, encoding="utf-8")
    entries = []
    for event in subs:
        if event.type == "Dialogue":  # Skip comments
            entries.append({
                "start": event.start / 1000.0,  # ms → seconds
                "end": event.end / 1000.0,
                "text": event.plaintext.strip()  # Auto-strips formatting
            })
    return entries

def detect_txt_subtitle_format(lines):
    # Detect SRT/VTT style timestamps with "-->"
    arrow_pattern = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}")
    for line in lines:
        if arrow_pattern.search(line):
            return "srt_vtt"

    # Detect SBV style timestamps "hh:mm:ss.xxx,hh:mm:ss.xxx"
    comma_pattern = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*,\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}")
    for line in lines:
        if comma_pattern.search(line):
            return "sbv"

    return None

def find_subtitles_file(path):
    supported_subtitles_file_types = get_common_parameters("supported_subtitles_file_types")
    subtitles_base_path = naming_convention(path, "subtitles_base")
    print(f"find_subtitles_file: subtitles_base_path = {subtitles_base_path}")

    for extension in supported_subtitles_file_types:
        candidate_path = f"{subtitles_base_path}.{extension}"
        if file_from_sta(candidate_path):
            print(f"find_subtitles_file: downloading {candidate_path = }")
            try:
                local_path = azure_blob_transfer(blobfilepath=candidate_path, operation="download")
                if local_path:
                    return candidate_path, local_path, extension
            except Exception as e:
                logging.error(f"Failed to download subtitles file [{candidate_path}]: {e}")
                continue
        else:
            if os.path.exists(candidate_path):
                return candidate_path, candidate_path, extension
            else:
                print(f"find_subtitles_file: subtitles file [{candidate_path}] does not exist locally")
                continue

    return None, None, None


def get_timing_format_subtitles(subtitles_json_raw):
    last_subtitle_end_time = subtitles_json_raw[-1]["end"]
    if isinstance(last_subtitle_end_time, str):
        last_subtitle_end_time = convert_hhmmss_mmss_to_seconds(last_subtitle_end_time)
    if last_subtitle_end_time > 6000:
        # if more than 100 minutes, use hhmmss
        return "hhmmss"
    return "mmss"

def get_improved_json_from_subtitles(subtitles_json_raw):
    timing_format = get_timing_format_subtitles(subtitles_json_raw)
    formatted_count = 0
    converted_count = 0
    improved_json = []
    for subtitle in subtitles_json_raw:
        start = subtitle.get("start")
        end = subtitle.get("end")
        if isinstance(start, str) and start.strip().isdigit() and len(start.strip()) in (4, 6):
            start_value = start.strip()
            formatted_count += 1
        else:
            start_value = convert_seconds_format(float(start), timing_format)
            converted_count += 1
        if isinstance(end, str) and end.strip().isdigit() and len(end.strip()) in (4, 6):
            end_value = end.strip()
            formatted_count += 1
        else:
            end_value = convert_seconds_format(float(end), timing_format)
            converted_count += 1
        improved_json.append({
            "start": start_value,
            "end": end_value,
            "text": get_common_parameters("text_key_for_improved_json_from_subtitles"),
            "imp": subtitle.get("imp", subtitle.get("text", ""))
        })
    logging.info(
        "get_improved_json_from_subtitles: "
        f"{formatted_count = }, {converted_count = }, {timing_format = }"
    )
    return improved_json

def main(path):
    setup_logging_with_appinsights(path)
    logging.info(f"Subtitles processing for {path = }")

    subtitle_path, subtitle_local_path, subtitle_extension = find_subtitles_file(path)
    if not subtitle_local_path:
        logging.info("No subtitles file found, skipping subtitles processing.")
        return

    try:
        subtitles_json_raw = parse_subtitles_file(subtitle_local_path, subtitle_extension)
    except Exception as e:
        logging.error(f"Failed to parse subtitles file [{subtitle_path}]: {e}")
        return

    if not subtitles_json_raw:
        logging.error(f"No subtitle entries found in [{subtitle_path}]")
        return
    
    if get_params("use_subtitles_as_is", path=path):
        combined_output_path = naming_convention(path, "combined")
        save_json_with_upload(combined_output_path, subtitles_json_raw)
        transcript_chunks = subtitles_json_raw
        logging.info(f"Subtitles combined saved to [{combined_output_path}]")
    else:
        transcript_words = get_words_timings_from_segments(subtitles_json_raw)
        transcript_sentences = combine_words_to_sentences(transcript_words, path)
        timing_format = get_timing_format(transcript_words)
        transcript_chunks = combine_sentences_to_chunks(transcript_sentences, path, timing_format)

        combined_output_path = naming_convention(path, "combined")
        save_json_with_upload(combined_output_path, transcript_chunks)
        logging.info(f"Converted subtitles combined saved to [{combined_output_path}]")

    number_conversion_instructions = build_number_conversion_instructions(path)
    try:
        transcript_chunks = improve_text_openai(
            episode=transcript_chunks,
            key_name="text",
            improved_text_key=get_params("improved_text_key"),
            custom_instructions=number_conversion_instructions,
            caffeinate=not file_from_sta(path)
        )
    except Exception as e:
        logging.error(f"Failed to improve subtitles text: [{e}]. Falling back to original text.")

    subtitles_json = get_improved_json_from_subtitles(transcript_chunks)

    output_path = naming_convention(path, "improved")
    save_json_with_upload(output_path, subtitles_json)
    logging.info(f"Converted subtitles saved to [{output_path}]")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="Path to the file to be processed")
    args = parser.parse_args()
    main(args.path)
