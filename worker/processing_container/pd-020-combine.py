from shared_functions import *


def get_words_timings_from_raw(transcript_raw):
    words_start = []
    for chunk in transcript_raw["segments"]:
        words_start += split_text_to_words_with_start_time(chunk)
        # print(split_text_to_words_with_start_time (chunk))
    return words_start


def split_text_to_words_with_start_time(chunk) -> dict: 
    """ 
    expect the following structure of the chunk:
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "id": { "type": "integer" },
    "seek": { "type": "integer" },
    "start": { "type": "number" },
    "end": { "type": "number" },
    "text": { "type": "string" },
    "tokens": {
      "type": "array",
      "items": { "type": "integer" }
    },
    "temperature": { "type": "number" },
    "avg_logprob": { "type": "number" },
    "compression_ratio": { "type": "number" },
    "no_speech_prob": { "type": "number" },
    "words": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "word": { "type": "string" },
          "start": { "type": "number" },
          "end": { "type": "number" },
          "probability": { "type": "number" }
        },
        "required": ["word", "start", "end", "probability"]
      }
    }
  },
  "required": ["id", "seek", "start", "end", "text", "tokens", "temperature", "avg_logprob", "compression_ratio", "no_speech_prob", "words"]
}

    """

    words_with_time = []
    for i, word in enumerate(chunk["words"]):
        word_data = {
            "word": word["word"].strip(),
            "start": word["start"],
            "end": word["end"],
        }
        words_with_time.append(word_data)

    return words_with_time



def split_text_to_words_with_start_time_old_based_on_chunk_timeings(chunk):
    """
    Splits a given chunk of text into a list of words, each with its start time.

    Parameters:
    chunk (dict): A dictionary containing the text, start, and end times.

    Returns:
    list: A list of dictionaries, each containing a word and its start time.
    """
    words = chunk["text"].strip().split()
    start_time = chunk["start"]
    end_time = chunk["end"]

    # Calculate the average duration for each word
    duration_per_word = (end_time - start_time) / len(words) if len(words) > 0 else 0

    words_with_time = []
    for i, word in enumerate(words):
        word_time = start_time + i * duration_per_word
        words_with_time.append({"word": word, "start": round(word_time, 2)})

    # # Test the function
    # chunk = {
    #     "id": 338,
    #     "seek": 176176,
    #     "start": 1761.76,
    #     "end": 1768.96,
    #     "text": " probably a few months longer than it would have been. But hey, we got it. Yeah. Over. No, we,",
    # }

    # words_with_start_time = split_text_to_words_with_start_time(chunk)
    # words_with_start_time

    return words_with_time



def main(path):

    setup_logging_with_appinsights(path)

    path_raw = naming_convention(path, "raw")


    import json
    transcript_raw = json.loads( get_palintext_content(path_raw) )
    transcript_words = get_words_timings_from_raw(transcript_raw)
    transcript_sentences = combine_words_to_sentences( transcript_words, path )  

    timing_format = get_timing_format(transcript_words)
    transcript_chunks = combine_sentences_to_chunks( transcript_sentences, path, timing_format )

    # save the transcript chunks
    path_combined = naming_convention(path, "combined")
    save_json_with_upload (path_combined, transcript_chunks)



    pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="Path to the file to be processed")
    args = parser.parse_args()

    main(args.path)