# pd-010-02-whisper-api-transcribe.py

import tempfile
from shared_functions import *


import os
import json
from pydub import AudioSegment, silence
from openai import OpenAI
import dotenv
import asyncio
import concurrent.futures
import re

dotenv.load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
print(f"openai_api_key: {openai_api_key[:5]}...")
client = OpenAI(api_key=openai_api_key)
print(f"client initialized: {client}")

# Constants
CHUNK_LENGTH_SEC = get_params("whisper_chunk_length_sec")
SILENCE_SEC = get_params("whisper_silence_sec")
WHISPER_API_MAX_SIZE_MB = get_params("whisper_api_max_size_mp3")
WHISPER_API_MAX_SIZE_BYTES = WHISPER_API_MAX_SIZE_MB * 1024 * 1024


def get_word_entry_value(word_entry, key):
    if isinstance(word_entry, dict):
        # print(f"get_word_entry_value: 0: word_entry is a dictionary: {word_entry}")
        return word_entry[key]
    else:
        # print(f"get_word_entry_value: 1: word_entry is not a dictionary: {word_entry}")
        return getattr(word_entry, key)

def set_word_entry_value(word_entry, key, value):
    if isinstance(word_entry, dict):
        word_entry[key] = value
    else:
        setattr(word_entry, key, value)

def tokenize_text(text) -> list[str]: 
    import re
    tokens = re.findall(r"\b\w[\w'-]*\b|[^\w\s]", text)
    return tokens

def add_punctuation_to_words(transcript):
    # Extract the words from transcript.words
    transcript_words_entries = transcript.words
    # Extract tokens from transcript.text, including punctuation
    tokens = tokenize_text(transcript.text)
    
    i = 0  # Index in transcript_words_entries
    j = 0  # Index in tokens

    new_words = []  # List to hold updated word entries

    while i < len(transcript_words_entries) and j < len(tokens):
        word_entry = transcript_words_entries[i]
        word = get_word_entry_value(word_entry, 'word')
        token = tokens[j]
        
        # If the words match (case-insensitive)
        if word.lower() == token.lower():
            # Use the token (which may include punctuation)
            new_entry = word_entry.copy()
            set_word_entry_value(new_entry, 'word', token)
            new_words.append(new_entry)
            i += 1
            j += 1
        # If token is punctuation
        elif re.match(r'[^\w\s]', token):
            # Append the punctuation to the previous word
            if new_words:
                last_word_entry = new_words[-1]
                last_word = get_word_entry_value(last_word_entry, 'word')
                set_word_entry_value(last_word_entry, 'word', last_word + token)
            else:
                # Should not happen, but just in case
                new_entry = word_entry.copy()
                set_word_entry_value(new_entry, 'word', token)
                new_words.append(new_entry)
            j += 1
        # If token is a contraction or hyphenated word
        else:
            # Try to merge multiple words in transcript_words to match the token
            combined_word = word
            combined_start = get_word_entry_value(word_entry, 'start')
            combined_end = get_word_entry_value(word_entry, 'end')
            next_i = i + 1
            while next_i < len(transcript_words_entries):
                next_word_entry = transcript_words_entries[next_i]
                combined_word += get_word_entry_value(next_word_entry, 'word')
                combined_end = get_word_entry_value(next_word_entry, 'end')
                if combined_word.lower() == token.lower().replace('-', '').replace("'", ''):
                    # Match found
                    new_entry = word_entry.copy()
                    set_word_entry_value(new_entry, 'word', token)
                    set_word_entry_value(new_entry, 'end', combined_end)
                    new_words.append(new_entry)
                    i = next_i + 1
                    j += 1
                    break
                next_i += 1
            else:
                # No match found, proceed with the current word
                new_words.append(word_entry)
                i += 1

    # Append any remaining punctuation tokens
    while j < len(tokens):
        token = tokens[j]
        if re.match(r'[^\w\s]', token):
            if new_words:
                last_word_entry = new_words[-1]
                last_word = get_word_entry_value(last_word_entry, 'word')
                set_word_entry_value(last_word_entry, 'word', last_word + token)
            else:
                new_entry = {'word': token, 'start': None, 'end': None}
                new_words.append(new_entry)
        j += 1

    # Update the transcript.words with the new words
    transcript.words = new_words
    return transcript



def transcribe_chunk(path, start_time, index, total_chunks):
    logging.info(f"transcribe_chunk: Starting transcription for chunk {index + 1}/{total_chunks}")


    prompt = None 
    # prompt_eng = "Please use punctuation."
    # TODO: Add prompt support - it should be in the same language as the audio
    # see more at https://platform.openai.com/docs/guides/speech-to-text/prompting

    source_language = None
    # TODO: Add source_language support - for now it looks like it's better without it
    # see more at https://platform.openai.com/docs/api-reference/audio/createTranscription
    
    with open(path, 'rb') as audio_file:
        try:
            transcript = client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["word"],
                prompt=prompt,
                language=source_language
            )
            logging.info(f"transcribe_chunk: Transcription completed for chunk {index + 1}/{total_chunks}")
        except Exception as e:
            logging.error(f"transcribe_chunk: Error transcribing chunk {index + 1}: {e}")
    # Add punctuation to words


    logging.info(f"transcribe_chunk: Adding punctuation to words...")
    transcript_words = [ {"word": word} for word in tokenize_text(transcript.text)]
    transcript_raw_words = transcript.words

    logging.info(f"transcript_words[:10]: {transcript_words[:10]}")
    logging.info(f"transcript_raw_words[:10]: {transcript_raw_words[:10]}")

    window_size = get_params("window_size_for_timesync")
    transcript_words_with_start_times = add_start_end_times_to_transcript(
        transcript_words, 
        transcript_raw_words, 
        window_size
        )

    logging.info(f"add_start_end_times_to_transcript: {transcript_words_with_start_times[:10]}")

    try:
        # transcript = add_punctuation_to_words(transcript)
        transcript.words = transcript_words_with_start_times
        print(transcript.words[:10])
    except Exception as e:
        logging.error(f"transcribe_chunk: Error adding punctuation to words: {e}")
        sys.exit(1)
    
    json_path = path + ".json"
    # print(transcript)
    with open(json_path, 'w') as f:
        json.dump(transcript.to_dict() , f, indent=4, ensure_ascii=False)
    logging.info(f"transcribe_chunk: Completed transcription for chunk {index + 1}/{total_chunks}")
    return transcript, start_time

def find_silence(audio, silence_sec):
    logging.info("Splitting audio to smaller chunks...")
    if get_params("whisper_silence_split") :
        res =  silence.detect_silence(audio, min_silence_len=silence_sec * 1000, silence_thresh=-40)
    else:
        res =  None
    return res

def split_audio_on_silence(audio, chunk_length_sec, silence_sec):
    logging.info("Splitting audio on silence...")
    silence_segments = find_silence(audio, silence_sec)
    chunks = []
    current_position = 0
    min_chunk_length_ms = 0.5 * chunk_length_sec * 1000
    max_chunk_length_ms = chunk_length_sec * 1000

    if silence_segments is None:
        # If no silence segments are found, treat the entire audio as a single segment
        silence_segments = [(len(audio), len(audio))]

    for start, end in silence_segments:
        middle = (start + end) // 2
        chunk_length = middle - current_position
        
        # Check if the chunk length is within the desired range
        if chunk_length >= min_chunk_length_ms and chunk_length <= max_chunk_length_ms:
            chunks.append((current_position, middle))
            current_position = middle
        elif chunk_length > max_chunk_length_ms:
            # If chunk is larger than max length, split it at regular intervals
            while current_position + max_chunk_length_ms < middle:
                next_position = current_position + max_chunk_length_ms
                chunks.append((current_position, next_position))
                current_position = next_position
            # Add the remaining part of the chunk
            if current_position < middle:
                chunks.append((current_position, middle))
                current_position = middle
    
    # Ensure any remaining audio after the last silence is handled
    if current_position < len(audio):
        while current_position + max_chunk_length_ms < len(audio):
            next_position = current_position + max_chunk_length_ms
            chunks.append((current_position, next_position))
            current_position = next_position
        # Add the final chunk
        if current_position < len(audio):
            chunks.append((current_position, len(audio)))
    
    return chunks



def split_audio(audio, chunk_length_sec, silence_sec):
    chunks = split_audio_on_silence(audio, chunk_length_sec, silence_sec)
    if not chunks:
        for i in range(0, len(audio), chunk_length_sec * 1000):
            chunks.append((i, min(i + chunk_length_sec * 1000, len(audio))))
    return chunks

async def process_audio_file(path):
    file_size = os.path.getsize(path)
    audio = AudioSegment.from_file(path)
    if file_size < WHISPER_API_MAX_SIZE_BYTES:
        logging.info(f"File size {file_size} is less than max size {WHISPER_API_MAX_SIZE_BYTES}, processing as a single chunk.")
        chunks = [(0, len(audio))]
    else:
        logging.info(f"File size {file_size} is greater than max size {WHISPER_API_MAX_SIZE_BYTES}, splitting into chunks.")
        chunks = split_audio(audio, CHUNK_LENGTH_SEC, SILENCE_SEC)
    tasks = []
    for index, (start, end) in enumerate(chunks):
        chunk_audio = audio[start:end]
        chunk_path = f"{path}_chunk_{start}_{end}.mp3"
        chunk_audio.export(chunk_path, format="mp3")
        tasks.append(asyncio.to_thread(transcribe_chunk, chunk_path, start, index, len(chunks)))
    results = await asyncio.gather(*tasks)
    return results

def merge_transcripts(transcripts):
    merged_transcript = {
        "text": "",
        "segments": [{"words": []}
        ]
    }
    for transcript, start_time in transcripts:
        logging.info(f"Processing transcript with start time {start_time}...")
        if hasattr(transcript, 'words'):
            transcript_words = transcript.words
            for i, word in enumerate(transcript_words):
                # print(f"before: {word}")
                previous_word_end = transcript_words[i-1]['end'] if i > 0 else start_time / 1000
                word['start'] = word['start'] + start_time / 1000 if 'start' in word else previous_word_end
                word['end'] = word['end'] + start_time / 1000 if 'end' in word else previous_word_end
                # print(f"after: {word}")
                merged_transcript["segments"][0]["words"].append(word)
        else:
            logging.info(f"Unexpected transcript format: {transcript}")
        
        if hasattr(transcript, 'text'):
            merged_transcript["text"] += transcript.text + " "
    
    return merged_transcript

async def main(path):


    setup_logging_with_appinsights(path)

    logging.info(f"Transcribing with whisper api for {path = }")
    
    transcripts = await process_audio_file(path)
    merged_transcript = merge_transcripts(transcripts)
    
    # Save the merged transcript as JSON
    output_path = naming_convention(path, "whisper-api-transcribe")
    with open(output_path, 'w') as f:
        json.dump(merged_transcript, f, indent=4, ensure_ascii=False)
    
    logging.info(f"Transcription saved to {output_path}")





if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    path = args.path

    asyncio.run(main(path))


    pass
