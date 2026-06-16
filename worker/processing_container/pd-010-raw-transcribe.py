import argparse
import json 
from shared_functions import *


import subprocess
import json
import os
import tempfile
from typing import Dict

def transcribe_with_word_timestamps(audio_path: str, model: str = "base.en") -> Dict:
    """
    Transcribes an audio file using OpenAI's Whisper model and saves the output in a temporary directory.

    Args:
    audio_path (str): The path to the audio file.
    model (str): The Whisper model to use. Default is 'base.en'.

    Returns:
    dict: The transcription result in JSON format or an error message.
    """
    # Create a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        # Extract the base name without extension to use for the output file
        base_name = os.path.splitext(os.path.basename(audio_path))[0]
        output_json_path = os.path.join(temp_dir, f"{base_name}.json")
        
        command = [
            "whisper",
            "--model", model,
            "--output_format", "json",
            "--word_timestamps", "True",
            "--output_dir", temp_dir,
            audio_path
        ]
        
        try:
            # Execute the Whisper command
            result = subprocess.run(command, check=True, text=True)
            # Log standard output and standard error
            logging.info(result.stdout)
            if result.stderr:
                logging.info(result.stderr)

            # Read the JSON output from the file
            with open(output_json_path, 'r') as file:
                return json.load(file)
        except subprocess.CalledProcessError as e:
            # Return error information if the command fails
            return {"error": str(e)}
        except FileNotFoundError:
            # Handle case where the output file is not found
            return {"error": "Output file not found"}


def split_text_to_words_with_start_time(chunk) -> list:
    words_with_time = []
    for word in chunk.get("words", []):
        word_data = {
            "word": word["word"].strip(),
            "start": word["start"],
            "end": word["end"],
        }
        words_with_time.append(word_data)

    return words_with_time


def get_words_timings_from_raw(transcript_raw: dict) -> list:
    words_start = []
    for chunk in transcript_raw.get("segments", []):
        words_start += split_text_to_words_with_start_time(chunk)
    return words_start


def build_transcript_text(transcript_raw: dict, path: str) -> str:
    transcript_words = get_words_timings_from_raw(transcript_raw)
    transcript_sentences = combine_words_to_sentences(transcript_words, path)
    return " ".join(sentence["text"] for sentence in transcript_sentences).strip()


def transcript_txt_exists(transcript_path: str) -> bool:
    if file_from_sta(transcript_path):
        return azure_blob_exists(transcript_path)

    return os.path.exists(transcript_path)


def copy_proofread_to_transcript_txt(proofread_path: str, transcript_path: str):
    transcript_text = get_palintext_content(proofread_path)

    if file_from_sta(transcript_path):
        local_path = get_local_file_path(transcript_path)
        with open(local_path, f"w", encoding=f"utf-8") as file:
            file.write(transcript_text)
        try:
            _ = azure_blob_transfer(
                blobfilepath=transcript_path,
                localfilepath=local_path,
                operation=f"upload",
                overwrite=False,
            )
        except ResourceExistsError:
            logging.info(f"Transcript text already exists, skipping proofread transcript upload: {transcript_path}")
            return transcript_path
        logging.info(f"Proofread text uploaded as transcript to: {transcript_path}")
    else:
        try:
            with open(transcript_path, f"x", encoding=f"utf-8") as file:
                file.write(transcript_text)
        except FileExistsError:
            logging.info(f"Transcript text already exists, skipping proofread transcript save: {transcript_path}")
            return transcript_path
        logging.info(f"Proofread text copied as transcript to: {transcript_path}")

    return transcript_path


def save_transcript_txt(transcript_raw: dict, path: str):
    transcript_path = naming_convention(path, f"transcript")
    if transcript_txt_exists(transcript_path):
        logging.info(f"Transcript text already exists, skipping generated transcript save: {transcript_path}")
        return transcript_path

    proofread_path = naming_convention(path, f"proofread")
    if transcript_txt_exists(proofread_path):
        logging.info(f"Proofread text exists, copying it to transcript: {proofread_path} -> {transcript_path}")
        return copy_proofread_to_transcript_txt(proofread_path, transcript_path)

    if not transcript_raw or "segments" not in transcript_raw:
        logging.info(f"save_transcript_txt: Transcript has no segments, skipping.")
        return None

    transcript_text = build_transcript_text(transcript_raw, path)

    if file_from_sta(path):
        local_path = get_local_file_path(transcript_path)
        with open(local_path, f"w", encoding=f"utf-8") as file:
            file.write(transcript_text)
        try:
            _ = azure_blob_transfer(
                blobfilepath=transcript_path,
                localfilepath=local_path,
                operation=f"upload",
                overwrite=False,
            )
        except ResourceExistsError:
            logging.info(f"Transcript text already exists, skipping generated transcript upload: {transcript_path}")
            return transcript_path
        logging.info(f"Transcript text uploaded to: {transcript_path}")
    else:
        try:
            with open(transcript_path, f"x", encoding=f"utf-8") as file:
                file.write(transcript_text)
        except FileExistsError:
            logging.info(f"Transcript text already exists, skipping generated transcript save: {transcript_path}")
            return transcript_path
        logging.info(f"Transcript text saved to: {transcript_path}")

    return transcript_path

# Example usage:
# result = transcribe_audio("dev/audio-samples/annoyted-e01-3min.mp3", model="medium.en")
# print(result)



def install_whisper():
    import subprocess
    import sys
    import pkg_resources
    package_name = 'openai-whisper'
    package_version = '20231117'

    try:
        # Check if the package is already installed
        pkg_resources.get_distribution(f"{package_name}=={package_version}")
        print(f"{package_name} version {package_version} is already installed.")
    except pkg_resources.DistributionNotFound:
        print(f"{package_name} version {package_version} is not installed. Installing now...")
        try:
            # Install the package using pip
            subprocess.check_call([sys.executable, "-m", "pip", "install", f"{package_name}=={package_version}"])
            print(f"{package_name} version {package_version} installation completed.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to install {package_name} version {package_version}: {e}")
            sys.exit(1)


## get Open AI transcript
def get_open_ai_transcript_text(audioFileName, openai_model_size, caffeinate = True):
    logging.info(f"Installing Open AI Whisper...")
    install_whisper()

    logging.info(f"Extracting transcript with Open AI Whisper locally. Model: [{openai_model_size}]")
    import whisper
    import time, datetime
    import os
    import subprocess
    from signal import SIGKILL
    caffeinate_proc = maybe_start_caffeinate(caffeinate)

    # Record the start time
    start_time = time.time()

    # open ai settings and inputs
    openai_model = whisper.load_model(openai_model_size)

    ## transcribe with open ai mp3 
    # old version with python library, no word timestamps
    # openai_result = openai_model.transcribe(audioFileName)
    # with option to get word timestamps
    openai_result = transcribe_with_word_timestamps(audioFileName, model = openai_model_size)

    # Record the end time
    end_time = time.time()
    # Calculate the elapsed time
    elapsed_time = end_time - start_time
    # Create a timedelta object from the elapsed time
    tdelta = datetime.timedelta(seconds=elapsed_time)
    # Extract the hours, minutes, and seconds from the timedelta object
    hours, remainder = divmod(tdelta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    # Format the elapsed time as hh:mm:ss
    elapsed_time_str = f"{hours:02}:{minutes:02}:{seconds:02}"
    # Print the elapsed time in hh:mm:ss format
    logging.info(f"Elapsed time: {elapsed_time_str} or  {elapsed_time:.4f} seconds")

    maybe_stop_caffeinate(caffeinate_proc)

    return openai_result
    ##########################################


## get Open AI transcript
def get_whisper_api_transcript_text(audioFileName, caffeinate = True):
    logging.info(f"Extracting transcript with Open AI Whisper API")
    import time, datetime
    import os
    import subprocess
    from signal import SIGKILL
    caffeinate_proc = maybe_start_caffeinate(caffeinate)

    # Record the start time
    start_time = time.time()
    # Get the current directory of the script
    current_directory = os.path.abspath(os.path.dirname(__file__))

    # transcribe with pd-010-02-whisper-api-transcribe.py
    pd007_cmd = [
        'python', f"{current_directory}/pd-010-02-whisper-api-transcribe.py",
        '-p', audioFileName,
    ]
    logging.info(f"Running whisper api transcribe: {pd007_cmd}")
    subprocess.run(pd007_cmd, check=True)

    transcription_local_path = naming_convention(audioFileName,"whisper-api-transcribe")
    with open(transcription_local_path, 'r') as f:
        whisper_api_result = json.load(f)
    openai_result = whisper_api_result

    # Record the end time
    end_time = time.time()
    # Calculate the elapsed time
    elapsed_time = end_time - start_time
    # Create a timedelta object from the elapsed time
    tdelta = datetime.timedelta(seconds=elapsed_time)
    # Extract the hours, minutes, and seconds from the timedelta object
    hours, remainder = divmod(tdelta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    # Format the elapsed time as hh:mm:ss
    elapsed_time_str = f"{hours:02}:{minutes:02}:{seconds:02}"
    # Print the elapsed time in hh:mm:ss format
    logging.info(f"Elapsed time: {elapsed_time_str} or  {elapsed_time:.4f} seconds")

    maybe_stop_caffeinate(caffeinate_proc)

    return openai_result
    ##########################################



def main(path, model_size):


    openai_model_size = model_size
    ## openai_model_size = "tiny.en"
    ## openai_model_size = "base.en"
    ## openai_model_size = "small.en"
    ## openai_model_size = "medium.en"
    ## openai_model_size = "large"

    local_path = get_local_path_with_download(path)


    setup_logging_with_appinsights(local_path)


    mp3_local_path = naming_convention(local_path, "mp3")

    if get_params("whisper_api", path=path):
        ## transcribe with whisper api
        openai_result = get_whisper_api_transcript_text (mp3_local_path, caffeinate = not file_from_sta(path))
    else:
        ## transcribe with whisper locally
        openai_result = get_open_ai_transcript_text (mp3_local_path, openai_model_size, caffeinate = not file_from_sta(path))



    #save resulting file
    # saveFile = f"{mp3_local_path[0:-4]}-raw.json"
    # saveBlob = f"{path[0:-4]}-raw.json"
    saveFile = naming_convention(mp3_local_path, "raw")
    saveBlob = naming_convention(path, "raw")
    write_json(openai_result, filename = saveFile) 
    logging.info(f"Result saved to: {saveFile}")
    if file_from_sta(path):
        _ = azure_blob_transfer(blobfilepath = saveBlob, operation = "upload")
        logging.info(f"Result uploaded to: {saveBlob}")

    save_transcript_txt(openai_result, path)





if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="path to file in mp3 or mp4, result will have naming convention 'raw'")
    parser.add_argument("-s", "--model-size", default="large", help="""whisper model size, default: large, 
                        see availbe models at https://github.com/openai/whisper#available-models-and-languages""")
    args = parser.parse_args()



    main(path = args.path, model_size = args.model_size)
