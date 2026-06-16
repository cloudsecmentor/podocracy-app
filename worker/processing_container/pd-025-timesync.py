import sys
import json
import os
import dotenv
import logging
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, TypeAdapter
from shared_functions import *

class Speakers(BaseModel):
    speakers: list[str] = Field(..., description="A list of unique speaker names identified from the transcript.")

def extract_first_n_words(text, n=7):
    # Split the text into lines
    lines = text.splitlines()
    
    # Initialize a list to store the results
    result = []
    
    # Iterate over each line
    for line in lines:
        # Strip leading/trailing whitespace and check if the line is not empty
        if line.strip():
            # Split the line into words
            words = line.split()
            # Take the first n words (or fewer if the line has less than n words)
            first_n_words = words[:n]
            # Join the words back into a string and add to the result list
            result.append(' '.join(first_n_words))
    
    return result

def get_speakers_list_from_transript_text(text, max_size=10000):
    import json
    from openai import OpenAI
    import os
    import dotenv
    dotenv.load_dotenv()
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = get_params("openai_model")
    text_first_n_words = extract_first_n_words(text)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": """
                You are an expert at extracting speaker names from a podcast transcript. Please identify all unique speakers mentioned and return them in the specified JSON structure.
                The speaker names in the transcript usually follow the pattern "\nSpeakerName:". Please read through the transcript and provide me with a list of all unique speaker names mentioned, considering any variations (full names, first names, etc.). Format the list as a JSON object, with each name as a separate element. If the same name appears in different formats, include each variation once. Exclude any names that do not follow the speaker name pattern, such as book authors, characters, or other non-speaker mentions. In cases of ambiguity where it's unclear if a name is that of a speaker, use your best judgment based on the context provided. 
                 Often seen speakers include: 'Brent Billings', 'Marty Solomon', 'Brent', 'Marty'

                If no speaker names following the specified pattern can be identified, please return an empty list.
                """},
                {"role": "user", "content": f"Here is the transcript to analyze:\n\n<transcript>{text_first_n_words[:max_size]}</transcript>"},
            ],
            **({"response_format": {"type": "json_object"}} | ({} if str(model).startswith("gpt-5") else {"temperature": 0}))
        )
        
        raw_response = response.choices[0].message.content
        try:
            # First, attempt to validate as the object model.
            validated_data = Speakers.model_validate_json(raw_response)
            res = validated_data.speakers
        except ValidationError:
            # If that fails, it might be a raw list. Validate as a list of strings.
            list_adapter = TypeAdapter(list[str])
            try:
                res = list_adapter.validate_json(raw_response)
            except ValidationError:
                logging.info(f"No speakers identified in transcript; continuing without speaker split")
                res = []

    except ValidationError as e:
        # This will catch validation errors from the second attempt if both fail.
        logging.error(f"Pydantic validation failed for both object and list formats: {e}")
        logging.error(f"Raw OpenAI response: {raw_response}")
        raise
    except Exception as e:
        logging.error(f"Error in get_speakers_list_from_transcript_text: {e}")
        raise

    return res

def get_speakers_list_from_transript_text_sk(kernel, text, max_size=10000):
    import json

    get_speakers = kernel.create_semantic_function(
        """
        I have a transcript of a podcast, and I need assistance in identifying all distinct speaker names. The speaker names in the transcript usually follow the pattern "\nSpeakerName:". Please read through the transcript and provide me with a list of all unique speaker names mentioned, considering any variations (full names, first names, etc.). Format the list as a JSON object, with each name as a separate element. If the same name appears in different formats, include each variation once. Exclude any names that do not follow the speaker name pattern, such as book authors, characters, or other non-speaker mentions. In cases of ambiguity where it's unclear if a name is that of a speaker, use your best judgment based on the context provided. 

        If no speaker names following the specified pattern can be identified, please return an empty list.

        Transcript:
        ###
        {{$input}}
        ###

        Format:
        [
            "Speaker Name"
        ]
        """
        )

    # Call the semantic function
    print(text[:max_size])
    response = get_speakers(text[:max_size])

    try:
        speakers_list = json.loads(response.result)
    except:
        raise Exception(f"Error in get_speakers_list_from_transript_text: {response}")
    
    logging.info(f"Speakers list: {speakers_list}")

    return speakers_list


# def split_text_by_speaker(text, speakers_list):
#     import re

#     # Regex pattern for extracting speaker and text
#     pattern = r"(.*?):\s*(.*)"

#     # Find all matches and create the desired structure
#     matches = re.findall(pattern, text)
#     formatted_transcript = [{"speaker": match[0], "text": match[1]} for match in matches if match[0] in speakers_list]

#     return formatted_transcript


def split_text_by_speaker(text, speakers_list):
    import re
    """
    Splits the text by speaker name followed by ': ', including all lines. Lines that
    do not start with a speaker name are appended to the previous speaker's text.

    :param text: The text to be split.
    :param speakers_list: A list of valid speaker names.
    :return: A list of dictionaries with 'speaker' and 'text' keys.
    """
    formatted_transcript = []
    if not speakers_list:
        return [{"speaker": "default", "text": text.strip()}]

    current_speaker = None
    current_text = ""

    for line in text.split('\n'):
        logging.info(f"line: {line[:50]}, {speakers_list=}, {current_speaker=}, {current_text=}")
        match = re.match(r'([^:]+):\s*(.*)', line)
        logging.info(f"{match=}")
        if match and match.group(1) in speakers_list:
            # If there's a current speaker, add their text to the transcript
            if current_speaker is not None:
                formatted_transcript.append({"speaker": current_speaker, "text": current_text.strip()})
            # Update current speaker and text
            current_speaker = match.group(1)
            current_text = match.group(2)
        else:
            # Append non-matching lines to the current text
            current_text += ' ' + line.strip()

    # Add the last speaker and their text to the transcript
    if current_speaker is not None:
        formatted_transcript.append({"speaker": current_speaker, "text": current_text.strip()})

    return formatted_transcript


def append_text_split_by_words(formatted_transcript_in):
    import copy
    formatted_transcript = copy.deepcopy(formatted_transcript_in) # Make a copy 
    for chunk in formatted_transcript:
        chunk["text_wrods"] = chunk["text"].split()

    return formatted_transcript


def split_text_into_chunks(text, max_length):
    """
    Splits a text into smaller chunks based on sentence boundaries,
    ensuring that each chunk is no longer than the specified maximum length.

    :param text: The text to be split.
    :param max_length: The maximum length of each chunk.
    :return: A list of text chunks.
    """
    import re

    # Split the text into sentences
    sentences = re.split(r'(?<=[.!?]) +', text)

    chunks = []
    current_chunk = ""

    for sentence in sentences:
        # Check if adding the next sentence would exceed the max length
        if len(current_chunk) + len(sentence) > max_length:
            # Add the current chunk to the list and start a new one
            chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            # Add the sentence to the current chunk
            current_chunk += ' ' + sentence

    # Add the last chunk if it's not empty
    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def get_words_from_transcript(formatted_transcript_with_words):
    words = []
    for i_chunk, chunk in enumerate(formatted_transcript_with_words):
        for j_word,word in enumerate(chunk["text_wrods"]):
            words.append(
                {
                    "word": word,
                    "chunk": i_chunk,
                    "word_in_chunk": j_word,
                }
            )
    return words

def split_text_to_words_with_start_end_time(chunk: dict):
    """
    Splits a given chunk of text into a list of words, each with its start time.

    Parameters:
    chunk (dict): A dictionary containing the text, start, and end times.

    Returns:
    list: A list of dictionaries, each containing a word and its start time.
    """
    words = chunk['words']

    words_with_time = []
    for i, word in enumerate(words):
        words_with_time.append({
            "word": word["word"].strip(), 
            "start": word["start"],
            "end": word["end"]
            })

    return words_with_time



def get_words_timings_from_raw(transcript_raw):
    words_start = []
    for chunk in transcript_raw["segments"]:
        words_start += split_text_to_words_with_start_end_time(chunk)
        # print(split_text_to_words_with_start_time (chunk))
    return words_start



def fill_missing_with_linear_regression_slope(data):
    import numpy as np
    # Extract known data points
    indices = [i for i, v in enumerate(data) if v is not None]
    values = [v for v in data if v is not None]

    # Check if there are enough points for regression
    if len(indices) < 2:
        # Not enough data to calculate a slope
        return data

    # Perform linear regression to find the slope
    A = np.vstack([indices, np.ones(len(indices))]).T
    m, _ = np.linalg.lstsq(A, values, rcond=None)[0]

    # Fill in missing values using the calculated slope
    first_known_index = indices[0]
    last_known_index = indices[-1]

    # Fill in the beginning and end
    for i in range(0, first_known_index):
        data[i] = data[first_known_index] - m * (first_known_index - i)
    for i in range(last_known_index + 1, len(data)):
        data[i] = data[last_known_index] + m * (i - last_known_index)

    # Fill in the middle
    for i in range(first_known_index, last_known_index + 1):
        if data[i] is None:
            data[i] = data[i - 1] + m

    return data


def split_speaker_text_to_chunks(formatted_transcript, max_chunk_length = 500):
    import copy
    formatted_transcript_new = []
    print(f"formatted_transcript: {len(formatted_transcript)}")
    for chunk in formatted_transcript:
        chunk_new = copy.deepcopy(chunk)
        text_chunks = split_text_into_chunks(chunk['text'], max_chunk_length)
        for text_chunk in text_chunks:
            chunk_current = copy.deepcopy(chunk_new)
            chunk_current['text'] = text_chunk
            if text_chunk.strip() != "":
                formatted_transcript_new.append(chunk_current)
            # print (chunk_new)
            # print (formatted_transcript_new)
        # print       (f"text_chunks: {len(text_chunks)}, formatted_transcript_new: {len(formatted_transcript_new)}")
        # print if debug
        logging.debug (f"text_chunks: {len(text_chunks)}, formatted_transcript_new: {len(formatted_transcript_new)}")
    return formatted_transcript_new



def append_start_end_time_to_chunks(formatted_transcript_speaker_chunked_in, transcript_words_with_start_times, timing_format):
    import copy
    formatted_transcript_speaker_chunked = copy.deepcopy(formatted_transcript_speaker_chunked_in)
 
    # add start and end times to speaker chunks
    for i, chunk in enumerate(formatted_transcript_speaker_chunked):
        start_time = None
        end_time = None
        
        for word in transcript_words_with_start_times:
            if word["chunk"] == i:
                if word["word_in_chunk"] == 0 and "start" in word:
                    start_time = word["start"]
                if "end" in word:
                    end_time = word["end"]

        if start_time is not None:
            chunk["start"] = convert_seconds_format(start_time, timing_format)
        if end_time is not None:
            chunk["end"] = convert_seconds_format(end_time, timing_format)
    
    return formatted_transcript_speaker_chunked


def align_timings(transcript_words, timing_key):
    times = [(word[timing_key] if timing_key in word else None) for word in transcript_words]
    logging.info(f"Number of words: {len(times)}, number of missing timings: {times.count(None)}")

    times = fill_missing_with_linear_regression_slope(times)

    time_delta_max = get_params("time_delta_max_for_timesync")
    for i in range(1, len(times)):
        if times[i] - times[i-1] > time_delta_max:
            logging.info(f"Warning: large time delta at {i} word: {times[i-1]} - {times[i]}")

    # Add the filled times back to the transcript
    for i, word in enumerate(transcript_words):
        if timing_key not in word or word[timing_key] is None:
            transcript_words[i][timing_key] = times[i]

    return transcript_words


def main(path):

    setup_logging_with_appinsights(path)
    path_raw = naming_convention(path, "raw")
    path_proofread = naming_convention(path, "proofread")
    path_transcript = naming_convention(path, "transcript")

    import json
    try:
        transcript_raw = json.loads( get_palintext_content(path_raw) )
    except Exception as e:
        logging.error(f"Can't get transcript raw: [{e}]")
        exit(1)


    transript_text = ""
    for text_path, label in ((path_proofread, "proofread"), (path_transcript, "transcript")):
        try:
            transript_text = get_palintext_content(text_path)
            logging.info(f"Using {label} text from {text_path}")
            break
        except Exception as e:
            logging.info(f"No {label} text at {text_path}: {e}")

    if not transript_text:
        logging.error("Can't get proofread or transcript text")
        exit(1)


    # # get speakers list using open ai
    speakers_list = get_speakers_list_from_transript_text(transript_text, max_size=120000)
    logging.info(f"Speakers list: {speakers_list}")

    # wait for N seconds
    import time
    time.sleep(5)

    # Split the text by speaker
    formatted_transcript = split_text_by_speaker(transript_text, speakers_list)
    logging.info(f"formatted_transcript: {formatted_transcript[:10]}")
    max_chunk_length = int(get_params("max_char_chunk", path=path))
    formatted_transcript_speaker_chunked = split_speaker_text_to_chunks(formatted_transcript, max_chunk_length)
    # print(formatted_transcript_speaker_chunked[:50])

    logging.info(f"formatted_transcript_speaker_chunked: {formatted_transcript_speaker_chunked[:10]}")

    formatted_transcript_with_words = append_text_split_by_words(formatted_transcript_speaker_chunked)
    transcript_words = get_words_from_transcript(formatted_transcript_with_words)
    logging.info(f"transcript_words: {transcript_words[:10]}")

    # get timings from raw transcript
    transcript_raw_words = get_words_timings_from_raw(transcript_raw)
    timing_format = get_timing_format(transcript_raw_words)
    logging.info(f"timing_format: {timing_format}")
    logging.info(f"transcript_raw_words: {transcript_raw_words[:10]}")

    # check whether start is always increasing
    is_start_always_increasing(transcript_raw_words)

    for i in range(1,len(transcript_raw_words)):
        if transcript_raw_words[i]["start"] < transcript_raw_words[i-1]["start"]:
            logging.info(f"Warning: start time is decreasing at {i} word: {transcript_raw_words[i-1]} - {transcript_raw_words[i]}")

    ## find matching words
    window_size = get_params("window_size_for_timesync")
    transcript_words_with_start_times = add_start_end_times_to_transcript(
        transcript_words, 
        transcript_raw_words, 
        window_size
        )
    logging.info(f"transcript_words_with_start_times: {transcript_words_with_start_times[:10]}")
    # check whether start is always increasing
    is_start_always_increasing(transcript_words_with_start_times)
    is_start_always_increasing(transcript_words)

    # Add the start and end times to the proofread transcript
    transcript_words_with_missing_start_times = align_timings(transcript_words_with_start_times, "start")
    logging.info(f"transcript_words_with_missing_start_times: {transcript_words_with_missing_start_times[:10]}")
    transcript_words_with_missing_start_end_times = align_timings(transcript_words_with_missing_start_times, "end")
    logging.info(f"transcript_words_with_missing_start_end_times: {transcript_words_with_missing_start_end_times[:10]}")


    # Add the start times to the transcript
    formatted_transcript_speaker_chunked_with_times = append_start_end_time_to_chunks(
        formatted_transcript_speaker_chunked, 
        transcript_words_with_start_times,
        timing_format
        )
    logging.info(f"formatted_transcript_speaker_chunked_with_times: {formatted_transcript_speaker_chunked_with_times[:10]}")

    path_combined = naming_convention(path, "combined")
    save_json_with_upload (path_combined, formatted_transcript_speaker_chunked_with_times)

    # # Save the transcript so far
    # write_json(formatted_transcript_speaker_chunked_with_times, f"{path_to_raw[:-5]}-spk.json")



if __name__ == "__main__":

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    main(args.path)



    pass

