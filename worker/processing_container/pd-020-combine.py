from shared_functions import *
from speaker_diarization import assign_speakers_to_words, diarize_speakers
from stt.schema import iter_transcript_words, replace_transcript_words


def get_words_timings_from_raw(transcript_raw):
    """Words of a canonical transcript, whichever provider produced it.

    Legacy raw files that only carry per-segment words still read correctly.
    """
    return iter_transcript_words(transcript_raw)


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

    if parse_legacy_bool(get_params("speaker_recognition", path=path)):
        number_of_speakers = max(1, min(20, int(get_params("number_of_speakers", path=path))))
        local_path = get_local_path_with_download(path)
        mp3_local_path = naming_convention(local_path, "mp3")
        speaker_turns = diarize_speakers(
            mp3_local_path,
            number_of_speakers,
            logging.getLogger(__name__),
        )
        words_with_speakers = assign_speakers_to_words(iter_transcript_words(transcript_raw), speaker_turns)
        replace_transcript_words(transcript_raw, words_with_speakers)
        transcript_raw["speaker_diarization"] = speaker_turns
        save_json_with_upload(path_raw, transcript_raw)
        save_json_with_upload(
            naming_convention(path, "diarization"),
            {
                "number_of_speakers": number_of_speakers,
                "turns": speaker_turns,
            },
        )

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