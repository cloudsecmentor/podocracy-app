
from datetime import datetime
import json
import logging
import math
import subprocess
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from dotenv import load_dotenv
import pytz
import shutil
import sys
import os
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))) # to access common functions
from common.shared_functions_common import get_common_parameters, is_supported_file_type


load_dotenv()
API_KEY = os.getenv("API_KEY")
API_URI = os.getenv("API_URI")

blob_storage_logger = logging.getLogger('azure')
blob_storage_logger.setLevel(logging.WARNING)


def maybe_start_caffeinate(enabled: bool = True):
    if not enabled:
        return None
    if not shutil.which("caffeinate"):
        return None
    return subprocess.Popen(["caffeinate", "-i"])


def maybe_stop_caffeinate(caffeinate_proc) -> None:
    if caffeinate_proc is None:
        return
    from signal import SIGKILL
    import os
    os.kill(caffeinate_proc.pid, SIGKILL)


def get_timestamp():
    from datetime import datetime as dt
    return dt.now().strftime("%Y-%m-%d-%H-%M-%S")


# utils.py
import inspect
import os

def get_main_filename():
    # Get the current frame
    current_frame = inspect.currentframe()
    
    # Traverse the stack to find the main module
    while current_frame:
        frame_info = inspect.getframeinfo(current_frame)
        if frame_info.function == '<module>':
            # Return the filename of the main module
            return os.path.basename(frame_info.filename)
        current_frame = current_frame.f_back

    return None


def setup_logging_old(path):

    file_path = get_local_file_path(path)

    import os
    import sys
    print(f"Setting up logging with {file_path = }")
    
    # Remove all handlers associated with the root logger
    for handler in logging.root.handlers[:]:
        print(f"Removing handler {handler = }")
        logging.root.removeHandler(handler)

    # Check if logging has already been configured
    if not logging.getLogger().hasHandlers():
        print(f"Setting up logging with {file_path = }")

        # Generate log file path with timestamp
        timestamp = get_timestamp()
        script_name = get_main_filename()
        base_path, ext = os.path.splitext(file_path)
        log_file_path = f"{base_path}_log_{script_name}_{timestamp}.log"
        # Debugging: Print the generated log file path
        print("Generated log file path:", log_file_path)

        # Configure logging to save to file and print to terminal
        logging.basicConfig(
            level=logging.INFO, 
            format='%(asctime)s %(levelname)s %(message)s', 
            handlers=[
                logging.FileHandler(log_file_path),
                logging.StreamHandler(sys.stdout)
            ])



        logging.info(f"Logging to [{log_file_path}]")

    else:
        print("Logging has already been configured")
        print(logging.getLogger().handlers)
        logging.error("Logging has already been configured")

    return logging.getLogger().handlers


def get_local_file_path(path):
    import os
    if file_from_sta(path):
        return f"{get_local_processing_directory()}/{os.path.basename(path)}"
    else:
        return path


## function to add to JSON
def write_json(data, filename='summary.json'):
    import json
    with open(filename, encoding='utf-8', mode='w+') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_final_line():
    print("Finished-conteiner")


def get_last_word_end_time(transcript_words):
    return transcript_words[-1]["end"]



def get_timing_format(transcript_words):
    last_word_end_time = get_last_word_end_time(transcript_words)
    if last_word_end_time > 6000:
        # if more than 100 minutes, use hhmmss
        return "hhmmss"
    else:
        return "mmss"


def combine_words_to_sentences(word_list, path):
    sentences = []
    current_sentence = ""
    start_time = 0.0
    max_char_chunk_per_sentence = int(get_params("max_char_chunk_per_sentence", path=path))
    print(f"combine_words_to_sentences: {max_char_chunk_per_sentence = }")

    for i, word_obj in enumerate(word_list):
        word = word_obj["word"]
        if current_sentence == "":
            start_time = word_obj["start"]  # Record the start time of the first word in the sentence
        end_time = word_obj["end"]  # Record the current end time of the sentence

        # Check if the word is punctuation
        if word in [".", "!", "?"]:
            # Append punctuation directly without a space
            current_sentence = current_sentence.rstrip() + word
        elif word in [",", ";", ":"]:
            # Append punctuation directly without a space
            current_sentence = current_sentence.rstrip() + word + " "
        else:
            # Add the word with a space
            current_sentence += word + " "

        current_sentence_object = {
            "start": start_time,
            "end": end_time,
            "text": current_sentence.strip()
        }

        # Check if the word ends with punctuation
        # Check if the current sentence length has reached the max character chunk limit
        # Check if the time difference to the next word is more than 2 seconds (if there is a next word)
        if word.endswith((".", "!", "?")) or \
           len(current_sentence) >= max_char_chunk_per_sentence or \
           (i + 1 < len(word_list) and word_list[i + 1]["start"] - end_time > get_params("delay_between_words_for_new_sentence_chunk")):

            sentences.append(current_sentence_object)
            current_sentence = ""  # Reset for the next sentence

    # Add the last sentence if it's not empty
    if current_sentence:
        sentences.append(current_sentence_object)

    return sentences


def split_text_to_words_with_times(text, start, end):
    words = [w for w in text.strip().split() if w]
    if not words:
        return []

    duration = max(end - start, 0)
    step = duration / len(words) if len(words) > 0 else 0
    words_with_time = []
    for i, word in enumerate(words):
        word_start = start + i * step
        word_end = start + (i + 1) * step if i < len(words) - 1 else end
        words_with_time.append({
            "word": word.strip(),
            "start": word_start,
            "end": word_end,
        })

    return words_with_time


def get_words_timings_from_segments(segments, text_key="text", start_key="start", end_key="end"):
    words_start = []
    for segment in segments:
        text = segment.get(text_key, "")
        start = segment.get(start_key, 0)
        end = segment.get(end_key, start)
        words_start += split_text_to_words_with_times(text, start, end)
    return words_start


def combine_sentences_to_chunks(sentence_list, path, timing_format):
    max_char_chunk = int(get_params("max_char_chunk", path=path))
    chunks = []
    current_chunk = ""
    chunk_start = 0.0
    chunk_end = 0.0

    for i, sentence in enumerate(sentence_list):
        print(f"combine_sentences_to_chunks: {i = }, {sentence = }")
        text = sentence["text"]
        sentence_start = sentence["start"]
        sentence_end = sentence["end"]

        # Start a new chunk if current is empty
        if current_chunk == "":
            chunk_start = sentence_start
            current_chunk = text + " "
            chunk_end = sentence_end
        else:
            # Check if adding the next sentence exceeds the max character limit
            # Check if there is a significant time gap before the next sentence
            print(f"combine_sentences_to_chunks: {i = }, {len(current_chunk) = }, {len(text) = }, {len(current_chunk + text) = }, {max_char_chunk = }")
            print(f"{len(current_chunk + text) > max_char_chunk = }")
            print(f"{sentence_start - chunk_end > get_params('delay_between_words_for_new_sentence_chunk') = }")
            print(f"{i + 1 < len(sentence_list) and sentence_list[i + 1]['start'] - sentence_end > get_params('delay_between_words_for_new_sentence_chunk') = }")
            if len(current_chunk + text) > max_char_chunk or \
                (sentence_start - chunk_end > get_params("delay_between_words_for_new_sentence_chunk")):
                chunks.append({
                    "start": convert_seconds_format(chunk_start, timing_format),
                    "end": convert_seconds_format(chunk_end, timing_format),
                    "text": current_chunk.strip()
                })
                current_chunk = ""
                current_chunk += text + " "
                chunk_end = sentence_end
                chunk_start = sentence_start
            else:
                current_chunk += text + " "
                chunk_end = sentence_end

    # Add the last chunk if it's not empty
    if current_chunk:
        chunks.append({
            "start": convert_seconds_format(chunk_start, timing_format),
            "end": convert_seconds_format(chunk_end, timing_format),
            "text": current_chunk.strip()
        })

    return chunks

def convert_seconds_format(seconds, format):
    if format == "hhmmss":
        return convert_seconds_hhmmss(seconds)
    else:
        return convert_seconds_mmss(seconds)
# convert seconds to string in format mmss
def convert_seconds_mmss (time_seconds):
    total_seconds = max(0, float(time_seconds))
    str_time = str(int(total_seconds//60)).zfill(2) + str(round(total_seconds%60)).zfill(2)
    return str_time

def convert_seconds_hhmmss (time_seconds):
    total_seconds = max(0, float(time_seconds))
    str_time = str(int(total_seconds//3600)).zfill(2) + str(int(total_seconds%3600//60)).zfill(2) + str(round(total_seconds%60)).zfill(2)
    return str_time

def convert_hhmmss_mmss_to_seconds(time: str):
    try:
        if len(time) == 4:
            seconds = int(time[-2:])
            minutes = int(time[-4:-2])
            return minutes * 60 + seconds
        elif len(time) == 6:
            seconds = int(time[-2:])
            minutes = int(time[-4:-2])
            hours = int(time[-6:-4])
            return hours * 3600 + minutes * 60 + seconds
        else:
            raise ValueError(f"convert_hhmmss_mmss_to_seconds: Invalid time format: {time}")
    except:
        raise ValueError(f"convert_hhmmss_mmss_to_seconds: Invalid time format: {time}")


def naming_convention(path, file_type):
    import os
    if not (is_supported_file_type(path) or path.endswith(".url")):
        raise ValueError(f"path must end with supported file type. [{path}]")
    else:
        # remove extension if it is supported file type or url
        path_without_ext = os.path.splitext(path)[0]
    
    match file_type:
        case "url":
            return f"{path_without_ext}.url"
        case "mp3":
            return f"{path_without_ext}.mp3"
        case "mp3_voiceover":
            return f"{path_without_ext}.voiceover.mp3"
        case "mp4":
            return f"{path_without_ext}.mp4"
        case "mp4_voiceover":
            return f"{path_without_ext}.voiceover.mp4"
        case "mp4_voiceover_preview":
            return f"{path_without_ext}.voiceover_preview.mp4"
        case "params":
            return f"{path_without_ext}.params.json"
        case "raw":
            return f"{path_without_ext}.raw.json"
        case "whisper-api-transcribe":
            return f"{path_without_ext}.whisperapitranscribe.json"
        case "proofread":
            return f"{path_without_ext}.proofread.txt"
        case "timesync": # this is to be used only for troubleshooting
            return f"{path_without_ext}.timesync.json"
        case "combined":
            return f"{path_without_ext}.combined.json"
        case "translated":
            return f"{path_without_ext}.translated.json"
        case "improved":
            return f"{path_without_ext}.improved.json"
        case "subtitles_base":
            return f"{path_without_ext}.subtitles"
        case "logs.zip":
            return f"{path_without_ext}.logs.zip"
        case "transcript":
            return f"{path_without_ext}.transcript.txt"
        case "directory":
            return f"{os.path.dirname(path)}"
        case "filename":
            return f"{os.path.basename(path)}"
        case "base_name":
            # filename without extension
            return f"{os.path.splitext(os.path.basename(path))[0]}"
        case _:
            raise ValueError(f"Unsupported file_type [{file_type}]")





def file_from_sta(path):
    # if path has pattern '"/blobServices/default/containers/<blobname>/blobs/<filname>.mp3"' it means it is form storage account
    if path.startswith('/blobServices/default/containers/'):
        try:
            logging.INFO(f"File '{path}' is from storage account")
        except:
            print(f"File '{path}' is from storage account")
        return True
    else:
        try:
            logging.INFO(f"File '{path}' is local")
        except:
            print(f"File '{path}' is local")
        return False

def get_local_processing_directory():
    path = "backend/processing_container/_processing_files_"
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def azure_blob_exists(blobfilepath):
    logging.info(f"azure_blob_exists({blobfilepath})")
    from azure.storage.blob import BlobServiceClient
    from dotenv import load_dotenv
    load_dotenv()

    AZURE_STORAGE_CONNECTION_STRING = os.getenv(f"AZURE_STORAGE_CONNECTION_STRING")
    blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

    parts = blobfilepath.split(f"/")
    container_name = parts[4]
    blob_name = parts[6]

    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    return blob_client.exists()


def azure_blob_transfer(blobfilepath, operation, localfilepath = None, overwrite = True):
    logging.info(f"azure_blob_transfer({blobfilepath}, {operation}, {localfilepath}")
    from azure.storage.blob import BlobServiceClient
    import os
    from dotenv import load_dotenv
    load_dotenv()
    # logging.getLogger('azure.storage.blob').setLevel(logging.ERROR)

    AZURE_STORAGE_CONNECTION_STRING = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

    # Parse the blob file path
    parts = blobfilepath.split("/")
    container_name = parts[4]
    blob_name = parts[6]

    # Set the local file path to blob_name if not provided
    if not localfilepath:
        localfilepath = f"{get_local_processing_directory()}/{blob_name}"

    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)

    if operation == "download":
        logging.info(f"Downloading file '{blob_name}' from container '{container_name}'")

        try:
            with open(localfilepath, "wb") as local_file:
                blob_data = blob_client.download_blob()
                blob_data.readinto(local_file)
            logging.info(f"File '{blob_name}' downloaded to '{localfilepath}'")
        except ResourceNotFoundError:
            logging.error(f"The blob '{blob_name}' was not found.")
            return None
        except Exception as e:
            logging.error(f"An error occurred while downloading the blob: {str(e)}")
            return None

    elif operation == "upload":
        logging.info(f"Uploading file '{localfilepath}' to container '{container_name}' as '{blob_name}'")
        with open(localfilepath, f"rb") as data:
            blob_client.upload_blob(data, overwrite=overwrite)
        logging.info(f"File '{localfilepath}' uploaded to '{blobfilepath}'")

    else:
        raise ValueError("Operation must be 'download' or 'upload'")

    return localfilepath


def get_params(parameter, path=None, processing_parameters_path='backend/processing_container/parameters.json'):
    # Load default parameters
    with open(processing_parameters_path, 'r') as file:
        default_params = json.load(file)    

    if path:
        dynamic_params = read_project_params(path)
        default_params.update(dynamic_params)
    
    # Return the requested parameter
    # print(f"get_params: {parameter=} \n{default_params=} \n{dynamic_params=}")
    # sys.exit()
    if parameter in default_params:
        value = None
        try:
            value = default_params[parameter]["value"]
        except:
            value = default_params[parameter]

        if value is not None:
            return value
        else:
            raise ValueError(f"Parameter [{parameter}] is empty")        

    else:
        raise ValueError(f"Unsupported parameter [{parameter}]")



def read_project_params(path):
    import json
    import os

    params_path = naming_convention(path, "params")

    # if from storage account, we need to download file first
    if file_from_sta(params_path):
        params_file_name = azure_blob_transfer(blobfilepath=params_path, operation="download")
    else:
        params_file_name = params_path

        # if file does not exist, use default params
        if not os.path.exists(params_file_name):
            logging.info (f"File '{params_file_name}' does not exist, using default_params")

            default_params = {
                "language": "RU",
                "stages_to_run": "all",
                }
            return default_params

    with open(params_file_name, encoding='utf-8') as f:
        params = json.load(f)

    return params


def parse_legacy_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def get_local_path_with_download(path):
    # if from storage account, we need to download file first
    if file_from_sta(path):
        local_path = azure_blob_transfer(blobfilepath=path, operation="download")
    else:
        local_path = path

    return local_path




def get_palintext_content(path):
    logging.info(f"get_palintext_content({path})")
    # if from storage account, we need to download file first
    if file_from_sta(path):
        logging.info(f"get_palintext_content: blobfilepath '{path}' is from storage account, downloading")
        file_name = azure_blob_transfer(blobfilepath=path, operation="download")
    else:
        file_name = path

    with open(file_name, encoding='utf-8') as f:
        content = f.read()

    return content



def save_json_with_upload(path, content):
    # if from storage account, we need to upload file, but first - save locally
    if file_from_sta(path):
        local_path = get_local_file_path(path)
        logging.info(f"Saving result locally prior to uploaded: {local_path}")
        write_json(content, filename = local_path)

        _ = azure_blob_transfer(blobfilepath = path, localfilepath= local_path,  operation = "upload")
        logging.info(f"Result uploaded to: {path}")
    else:
        write_json(content, filename = path) 
        logging.info(f"Result saved to: {path}")

    return None


def copy_or_upload(source_path, destination_path):
    logging.info(f"copy_or_upload({source_path}, {destination_path})")
    # if from storage account, we need to upload file, but first - save locally
    if file_from_sta(destination_path):
        local_path = get_local_file_path(destination_path)
        logging.info(f"Copying result locally prior to uploaded: {local_path}")
        import shutil
        try:
            shutil.copy(source_path, local_path)
        except:
            logging.info(f"Files [{source_path}] and [{local_path}] are the same, skipping copy")

        _ = azure_blob_transfer(blobfilepath = destination_path, localfilepath= local_path,  operation = "upload")
        logging.info(f"Result uploaded to: {destination_path}")
    else:
        import shutil
        try:
            shutil.copy(source_path, destination_path)
        except:
            logging.info(f"Files [{source_path}] and [{destination_path}] are the same, skipping copy")
        logging.info(f"Result copied to: {destination_path}")

    return None


def mp4_processing(path):
    # if path ends with mp4, return True
    if path.endswith(".mp4"):
        return True
    else:
        return False

def video_processing(path):
    # if extension is in supported_video_file_types, return True
    supported_video_file_types = get_common_parameters("supported_video_file_types")
    extension = path.split(".")[-1]
    if extension in supported_video_file_types:
        return True
    else:
        return False

def mp3_processing(path):
    # if path ends with mp3, return True
    if path.endswith(".mp3"):
        return True
    else:
        return False


def url_processing(path):
    # if path ends with url, return True
    if path.endswith(".url"):
        return True
    else:
        return False
    

def extract_audio(in_file_path, audio_file_path):
    ffmpeg_cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'quiet',
        '-i', in_file_path,
        '-vn',
        '-acodec', 'libmp3lame',
        '-ab', '192k',
        '-ar', '44100',
        '-y',
        audio_file_path
    ]
    logging.info(f"extract_audio: ffmpeg_cmd = {' '.join(ffmpeg_cmd)}")   
    subprocess.run(ffmpeg_cmd, check=True)
    return True

def extract_audio_and_upload(path, local_path):
    """
    Preprocesses the audio file by extracting and uploading the MP3.

    Args:
        path (str): The original path to the MP3 file.
        local_path (str): The local filesystem path to the MP3 file.
    """
    logging.info(f"extract_audio_and_upload: Preprocessing file: {path=}, {local_path=}")
    mp3_local_path = naming_convention(local_path, "mp3")
    mp3_path = naming_convention(path, "mp3")
    res = extract_audio(local_path, mp3_local_path)
    if not res:
        logging.error(f"extract_audio_and_upload: Failed to extract audio from {local_path}")
        return None 
    copy_or_upload(mp3_local_path, mp3_path)
    return mp3_local_path


def merge_video_audio(in_video_file, in_audio_file, out_video_file):
    ffmpeg_cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'quiet', 
        '-i', in_video_file,
        '-i', in_audio_file,
        "-c:v", "libx264",  # Re-encode video to H.264
        "-c:a", "aac",      # Re-encode audio to AAC
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-y',
        # '-shortest', ## to allow full audio length
        out_video_file
    ]
    logging.info(f"merge_video_audio: ffmpeg_cmd = {' '.join(ffmpeg_cmd)}")
    subprocess.run(ffmpeg_cmd, check=True)
    return True


def create_preview_version(
    in_video_file,
    out_video_file,
    max_file_size_mb,
    limit_preview_length_minutes=None
):
    """
    Creates a compressed preview version of a video file, optionally 
    trimming it to a specified length (in minutes) before compression.

    Parameters:
        in_video_file (str): Path to the input video file.
        out_video_file (str): Path to save the compressed video file.
        max_file_size_mb (int): Target maximum file size in megabytes (MB).
        limit_preview_length_minutes (float, optional): If set, the video will
            be trimmed (in minutes) to this length before compression.

    Returns:
        bool: True if successful, raises Exception otherwise.
    """
    # Convert max file size from MB to bytes
    max_file_size = max_file_size_mb * 1024 * 1024  # MB to bytes

    # Get total video duration using ffprobe
    ffprobe_cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        in_video_file
    ]
    
    try:
        logging.info(f"create_preview_version: ffprobe_cmd = {' '.join(ffprobe_cmd)}")
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        original_duration = float(result.stdout.strip())  # Duration in seconds
    except subprocess.CalledProcessError as e:
        raise Exception(f"Error retrieving video duration: {e.stderr}") from e

    # Determine if we need to trim the video
    if limit_preview_length_minutes is not None:
        limit_duration_seconds = limit_preview_length_minutes * 60
        # We'll use the smaller of the original duration or the limit
        effective_duration = min(original_duration, limit_duration_seconds)
    else:
        effective_duration = original_duration

    # Calculate target bitrate (in bits per second) based on effective duration
    target_bitrate = (max_file_size * 8) / effective_duration  # bytes -> bits, / duration
    target_bitrate_kbps = math.floor(target_bitrate / 1000)  # convert to kbps
    
    if target_bitrate_kbps <= 0:
        raise ValueError(
            "Target bitrate is too low. Increase max_file_size or check input video duration."
        )

    # Build ffmpeg command
    ffmpeg_cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'quiet',
        '-i', in_video_file,
        # If we're trimming, specify the `-t` parameter
        *(['-t', str(effective_duration)] if limit_preview_length_minutes is not None else []),
        '-vf', 'scale=1280:720',  # Resize video to 720p
        '-b:v', f'{target_bitrate_kbps}k',  # Set target video bitrate
        '-bufsize', f'{target_bitrate_kbps * 2}k',  # Set buffer size
        '-maxrate', f'{target_bitrate_kbps}k',     # Set max bitrate
        '-y',
        out_video_file
    ]
    
    try:
        logging.info(f"create_preview_version: ffmpeg_cmd = {' '.join(ffmpeg_cmd)}")
        # Run the command in silent mode
        with open(os.devnull, 'w') as silent_output:
            result = subprocess.run(ffmpeg_cmd, stdout=silent_output, stderr=subprocess.PIPE, text=True, check=True)
        if result.returncode != 0:
            logging.error(f"Error occurred during compression: {result.stderr}")
            raise Exception(f"FFmpeg returned non-zero exit code: {result.returncode}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Error compressing video: {e.stderr}") from e

    return True


class CustomFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, tzinfo=None, logging_max_characters=None):
        super().__init__(fmt, datefmt)
        self.tzinfo = tzinfo
        self.logging_max_characters = logging_max_characters

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, self.tzinfo)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()

    def format(self, record):
        # Use the base class format to create the initial message
        formatted_message = super().format(record)

        # Truncate the message if it exceeds logging_max_characters
        if self.logging_max_characters and len(formatted_message) > self.logging_max_characters:
            formatted_message = formatted_message[:self.logging_max_characters] + '...'

        return formatted_message


def truncate_message(message, max_length=100):
    return json.dumps(message)[:max_length] + '...' if len(message) > max_length else message


def setup_logging_with_appinsights(path="", timezone="Europe/Stockholm", logging_max_characters=1000):

    file_path = get_local_file_path(path)

    print(f"Setting up logging with {file_path = }")

    # Remove all handlers associated with the root logger
    for handler in logging.root.handlers[:]:
        print(f"Removing handler {handler = }")
        logging.root.removeHandler(handler)

    # Check if logging has already been configured
    if not logging.getLogger().hasHandlers():
        print(f"Setting up logging with {file_path = }")

        # Generate log file path with timestamp
        timestamp = get_timestamp()
        script_name = get_main_filename()
        base_path, ext = os.path.splitext(file_path)
        _log_file_path = f"{base_path}_log_{script_name}_{timestamp}.log"
        ## adding .log/ folder to log_file_path befor the file name
        # current path from log_file_path
        base_log_path = os.path.dirname(_log_file_path)
        new_log_path = f"{base_log_path}/.log" if base_log_path else ".log"
        # create directory if not resent
        if not os.path.exists(new_log_path):
            os.makedirs(new_log_path)

        
        log_file_name = os.path.basename(_log_file_path)
        log_file_path = f"{new_log_path}/{log_file_name}"
        # Debugging: Print the generated log file path
        print("Generated log file path:", log_file_path)

        # Set up custom formatter with timezone and logging character limit
        tzinfo = pytz.timezone(timezone)
        formatter = CustomFormatter(
            fmt='%(asctime)s  %(levelname)s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S %Z%z',
            tzinfo=tzinfo,
            logging_max_characters=logging_max_characters
        )

        # Configure logging to save to file and print to terminal
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)

        logging.basicConfig(
            level=logging.INFO,
            handlers=[file_handler, stream_handler]
        )

        import dotenv
        dotenv.load_dotenv()

        APPLICATIONINSIGHTS_CONNECTION_STRING=os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
        if APPLICATIONINSIGHTS_CONNECTION_STRING:
            # note that when is triggered from API, APPLICATIONINSIGHTS_CONNECTION_STRING is not set
            from opencensus.ext.azure.log_exporter import AzureLogHandler
            try:
                # Callback function to append '_hello' to each log message telemetry
                def truncate_message(envelope):
                    m = envelope.data.baseData.message
                    envelope.data.baseData.message = m[:logging_max_characters] + '...' if len(m) > logging_max_characters else m
                    return True

                handler = AzureLogHandler(connection_string=APPLICATIONINSIGHTS_CONNECTION_STRING)
                handler.add_telemetry_processor(truncate_message)

                logging.getLogger().addHandler(handler)
                logging.info(f"setup_logging_with_appinsights: Logging to Application Insights is enabled")
            except Exception as e:
                logging.error(f"setup_logging_with_appinsights: Error: {e}")

        logging.info(f"Logging to [{log_file_path}]")

    else:
        print("Logging has already been configured")
        print(logging.getLogger().handlers)
        logging.error("Logging has already been configured")

    return logging.getLogger()

def post_data_to_api_backend(url: str, body: dict | None = None, debug: bool = True):

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "accept": "application/json"
    }
    payload = body or {}

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        if debug:
            logging.info(f"post_data_to_api_backend {url = } response: {data = }")
        return data
    except requests.exceptions.RequestException as e:
        logging.info(f"Error post_data_to_api_backend: {url = } Error: {e}")
        # Handle error appropriately
        return e


def get_data_from_api_backend(url: str, params: dict | None = None, debug: bool = True):

    headers = {
        "X-Api-Key": f"{API_KEY}",
        "accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        if debug:
            logging.info(f"get_data_from_api_backend {url = } response: {data = }")
        return data
    except requests.exceptions.RequestException as e:
        logging.info(f"Error get_data_from_api_backend: {url = } Error: {e}")
        return None


def update_status(user_id: str, project_id: str, state: str, progress: int):
    url = f"{API_URI}/v1/internal/projects/status"
    body = {
        "user_id": user_id,
        "project_id": project_id,
        "state": state,
        "progress": progress,
    }
    return post_data_to_api_backend(url=url, body=body)


def improve_text_openai(episode, key_name, improved_text_key="imp", sleep_time=0.5, custom_instructions="", caffeinate=True):
    import time
    import copy
    episode_new = copy.deepcopy(episode)
    remove_key_name = True  # TODO: add this as a parameter
    from tqdm import tqdm
    import random
    import string
    import subprocess
    from signal import SIGKILL

    caffeinate_proc = maybe_start_caffeinate(caffeinate)

    max_length_megachunk = int(get_params("improve_output_tokens_max") * get_params("improve_tokens_to_use_for_input_fraction"))
    # Limit megachunk to a max length of 90 to avoid exceeding schema limits
    max_megachunk_size = 90

    megachunks = []
    current_megachunk = []
    current_length = 0

    # Group chunks into megachunks
    for chunk in episode_new:
        chunk_length = len(chunk[key_name])
        
        # Check if the current megachunk is full or would exceed size limits
        if current_length + chunk_length <= max_length_megachunk and len(current_megachunk) < max_megachunk_size:
            current_megachunk.append(chunk)
            current_length += chunk_length
        else:
            megachunks.append(current_megachunk)
            current_megachunk = [chunk]
            current_length = chunk_length
    if current_megachunk:
        megachunks.append(current_megachunk)

    for megachunk in tqdm(megachunks):
        megachunk_key = {}
        megachunk_text = {}
        random_key_to_chunk = {}
        for chunk in megachunk:
            # Generate a random 7-character key
            random_key = ''.join(random.choices(string.ascii_letters, k=7))
            while random_key in megachunk_key:
                random_key = ''.join(random.choices(string.ascii_letters, k=7))
            megachunk_key[random_key] = chunk[key_name]
            megachunk_text[random_key] = chunk["text"]
            random_key_to_chunk[random_key] = chunk

        # Create Pydantic schema dynamically
        from pydantic import BaseModel, create_model
        from typing import Dict

        # Prepare fields for the dynamic model
        fields = {key: (str, ...) for key in megachunk_key.keys()}
        MegachunkModel = create_model('MegachunkModel', **fields)

        class Step(BaseModel):
            explanation: str
            output: str

        class MathResponse(BaseModel):
            steps: list[Step]
            final_answer: str

        # Pass megachunk_* dictionaries to improve_text_openai_chunk_2409
        try:
            improved_text_dict = improve_text_openai_chunk_2409(megachunk_key, megachunk_text, custom_instructions, MegachunkModel)
        except Exception as e:
            logging.error(f"Error in improve_text_openai_chunk_2409: [{e}]")
            raise e

        print(improved_text_dict)

        # Update the chunks with the improved text
        improved_text_dict = improved_text_dict.dict()  # Convert the model instance to a dictionary

        for random_key, improved_text in improved_text_dict.items():
            chunk = random_key_to_chunk[random_key]
            chunk[improved_text_key] = improved_text
            if remove_key_name:
                del chunk[key_name]
        time.sleep(sleep_time)

    maybe_stop_caffeinate(caffeinate_proc)

    return episode_new


def improve_text_openai_chunk_2409(megachunk_key, megachunk_text, custom_instructions, megachunk_model):
    import json
    from openai import OpenAI
    import os
    import dotenv
    dotenv.load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    model = get_params("improve_openai_model")

    # Prepare system and user messages
    system_message = "You are a helpful assistant designed to proofread and improve text quality."

    custom_instructions_text = f"- Pay special attention to the following instructions:\n{custom_instructions}" if custom_instructions else ""

    user_message = f"""
You will receive two dictionaries: one containing texts to improve, and another containing original texts. 
Your task is to improve the texts in the first dictionary, considering the original texts for context.

Texts to improve (in JSON format):
{json.dumps(megachunk_key, ensure_ascii=False)}

Original texts (in JSON format):
{json.dumps(megachunk_text, ensure_ascii=False)}

First, identify the language of the text to improve.

Then, carefully proofread each text, looking for ways to improve its readability, 
naturalness, and clarity. Keep the following in mind:
- Convert any numbers into words.
- Correct any grammatical errors, awkward phrasing, or non-native language patterns.
- Make the text sound as natural and concise as possible while still retaining the original meaning.
- Remove filler words (like "um", "uh", "you know", etc.) and fix informal or ungrammatical spoken language forms where it improves clarity and flow.
- Preserve the core meaning and key content of the original text - do not embellish or change the meaning.
{custom_instructions_text}

**Important Instructions:**
- For each key in the 'Texts to improve' dictionary, provide the improved text as the value in the output.
- If the improved text is an empty string after improvements (e.g., due to removal of filler words), include it as an empty string in the output.
- Do not skip any keys or change the order of the entries.
- Ensure that the output is a dictionary in JSON format with the same keys as the 'Texts to improve' dictionary.
- The number of entries in your output must match the number of entries in the 'Texts to improve' dictionary.

When you are finished, provide the improved texts as a JSON dictionary with the same keys as the 'Texts to improve' dictionary.
"""

    # gpt-5 only supports default temperature; avoid passing temperature explicitly
    _kwargs = {"response_format": megachunk_model}
    if not str(model).startswith("gpt-5"):
        _kwargs["temperature"] = get_params("improve_openai_temperature")

    response = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ],
        **_kwargs
    )

    # Extract the assistant's message
    message = response.choices[0].message

    if message.parsed:
        print(message.parsed)
        improved_texts = message.parsed
    elif message.refusal:
        # handle refusal
        logging.error(f"Refusal: [{message.refusal}]")
        raise Exception(f"Refusal: [{message.refusal}]")        

    return improved_texts
    



def word_entry_get(word_entry, key):
    if isinstance(word_entry, dict):
        return word_entry[key]
    return getattr(word_entry, key)


def normalize_word_entries(words):
    return [
        word if isinstance(word, dict) else {
            "word": word_entry_get(word, "word"),
            "start": word_entry_get(word, "start"),
            "end": word_entry_get(word, "end"),
        }
        for word in words
    ]


def add_start_end_times_to_transcript(transcript_redacted_words, transcript_raw_words, window_size):
    import string
    """
    Compares words from transcript_words with transcript_raw_words within a specified window size.
    If 60% of the words match, adds the 'start' value from transcript_raw_words to the corresponding words in transcript_words.
    Punctuation is stripped off from the words before comparison.

    Parameters:
    transcript_words (list): List of word dictionaries from the processed transcript.
    transcript_raw_words (list): List of word dictionaries from the raw transcript.
    window_size (int): The number of words to consider in each comparison window.

    Returns:
    list: Updated transcript_words with 'start' times added where matches are found.
    """
    import copy
    transcript_words = copy.deepcopy(transcript_redacted_words) # Make a copy of the transcript_words to avoid modifying the original list
    transcript_raw_words = normalize_word_entries(transcript_raw_words)


    # how_many_windows_to_search
    how_many_windows_to_search = 10


    # Helper function to compare two windows of words
    def is_match(window1, window2, should_match = 0.8):
        matching_words = sum(1 for w1, w2 in zip(window1, window2) 
                             if w1['word'].lower().strip(string.punctuation) == w2['word'].lower().strip(string.punctuation))
        return matching_words >= should_match * window_size

    # Loop through transcript_words in windows of size 'window_size'
    i = 0
    j_latest_match = 0
    while i < len(transcript_words) - window_size:
    # for i in range(len(transcript_words) - window_size + 1):
        # pring if debug
        if 'start' in transcript_words[i]:
            logging.debug (f"i: {i}, j_latest_match: {j_latest_match}, is_start_always_increasing:{is_start_always_increasing(transcript_words)}")
        # print       (f"i: {i}, j_latest_match: {j_latest_match}, is_start_always_increasing:{is_start_always_increasing(transcript_words)}")
        window_transcript = transcript_words[i:i + window_size]

        # Compare with each window in transcript_raw_words
        j_max = min(j_latest_match + window_size*how_many_windows_to_search, len(transcript_raw_words) - window_size)
        for j in range(j_latest_match, j_max):
            window_raw = transcript_raw_words[j:j + window_size]

            if is_match(window_transcript, window_raw):
                j_latest_match = j
                # If a match is found, update 'start' time for each word in the window
                for k, word in enumerate(window_transcript):
                    #check if the start time is the same
                    if 'start' in transcript_words[i + k]:
                        continue
                        # if transcript_words[i + k]['start'] != window_raw[k]['start']:
                        #     print(f"{i + k},{j+k},{transcript_words[i + k]['start']}, {window_raw[k]['start']}")
                        #     raise ValueError (f"Warning: start time for [{word['word']}] is different in the raw transcript.")
                    if transcript_words[i + k]['word'].lower().strip(string.punctuation) == window_raw[k]['word'].lower().strip(string.punctuation):
                        transcript_words[i + k]['start'] = window_raw[k]['start']
                        transcript_words[i + k]['end'] = window_raw[k]['end']
                break
        i += int(window_size / 2) # Move to the next window with an overlap of 50%

    return transcript_words


def is_start_always_increasing(data):
    last_valid_start = None  # Initialize a variable to keep track of the last valid start time

    for i in range(0, len(data)):
        # Get the start time of the current word, defaulting to None if not present
        current_start = data[i].get("start")

        # Check if the current start time is available
        if current_start is not None:
            # If there is a last valid start time, compare it with the current start time
            if last_valid_start is not None and current_start < last_valid_start:
                return False  # Return False as soon as a decreasing start time is found
            # Update the last valid start time
            last_valid_start = current_start

    # Usage example:
    # result = is_start_time_always_increasing(your_data_list)


    return True  # Return True if no decreasing start time was found
def get_user_id_from_sta_path(path):
    # storage path is in the format: /blobServices/default/containers/user_id/blobs/filename
    user_id = path.split('/')[4]
    return user_id
