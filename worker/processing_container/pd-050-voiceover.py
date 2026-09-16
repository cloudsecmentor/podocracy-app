import glob
import json
import re
import zipfile
from pathlib import Path
from tenacity import retry, wait_exponential, stop_after_attempt, before_sleep_log
from shared_functions import *
from azure.storage.blob import BlobServiceClient
from frontend.shared_functions_frontend import get_container_name_from_id
from common import segment_store as seg
from portal_status import portal_project_dir

def create_timestamped_directory(base="content"):
    import os
    from datetime import datetime as dt
    # print (base)
    # Get the current timestamp
    timestamp = dt.now().strftime('%Y%m%d_%H%M%S')
    # Construct the directory path
    if os.path.isfile(base):
        dir_path = os.path.join(os.path.dirname(base), timestamp)
    else:
        dir_path = os.path.join(base, timestamp)
        

    # Create the directory
    os.makedirs(dir_path, exist_ok=True)

    logging.info(f"Directory created at: {dir_path}")
    return dir_path


def get_voice_name(path):
    try:
        voice = get_params("voice", path=path).lower()
        logging.info(f"get_voice_name: Voice found in params: {voice}")
    except Exception as e:
        logging.info(f"get_voice_name: Voice not found in params, using default [alloy], error: {e}")
        voice = "alloy"
    return voice


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(10),
    reraise=True,
    before_sleep=before_sleep_log(logging, logging.INFO)
)
def generate_openai_tts(path, text, speech_file_path, voice):
    tts_api = str(get_params("tts_api", path=path) or "openai").lower()
    if tts_api == "elevenlabs":
        logging.info(f"Using ElevenLabs TTS API for {speech_file_path}")
        return tts_elevenlabs(text, speech_file_path)
    if tts_api == "vibevoice":
        logging.info(f"Using local VibeVoice TTS server for {speech_file_path}")
        return tts_vibevoice(path, text, speech_file_path, voice)

    from openai import OpenAI
    import os
    import dotenv
    dotenv.load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    model = get_params("openai_model_tts")
    # model = "gpt-4o-mini-tts" # "tts-1"    # "tts-1-hd"

    with client.audio.speech.with_streaming_response.create(
        model=model,
        voice=voice,
        input=text
    ) as response:
        response.stream_to_file(speech_file_path)

    return None


def tts_vibevoice(path: str, text: str, speech_file_path: str, voice: str) -> None:
    """Local, OpenAI-compatible TTS server. This legacy path writes .ogg, so ask the
    server for ogg rather than the mp3 the portal worker requests."""
    import os

    import requests

    base_url = (os.getenv("VIBEVOICE_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("VIBEVOICE_BASE_URL is not set")
    if base_url.split(":", 1)[0].lower() not in ("http", "https"):
        raise ValueError("VIBEVOICE_BASE_URL must use http or https")

    headers = {"Content-Type": "application/json", "Accept": "audio/ogg"}
    api_key = (os.getenv("VIBEVOICE_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def param(name):
        # get_params raises on an older parameters.json that predates these keys.
        try:
            return get_params(name, path=path)
        except Exception:
            return ""

    payload = {
        "input": text,
        "voice": voice or os.getenv("VIBEVOICE_TTS_VOICE") or "SEBBE",
        "model": param("vibevoice_model") or os.getenv("VIBEVOICE_TTS_MODEL") or "7B",
        "response_format": "ogg",
    }
    for key, env_name in (("speed", "VIBEVOICE_SPEED"), ("cfg_scale", "VIBEVOICE_CFG_SCALE")):
        raw = str(param(f"vibevoice_{key}") or os.getenv(env_name) or "").strip()
        if raw:
            payload[key] = float(raw)

    timeout = float((os.getenv("VIBEVOICE_TIMEOUT_SECONDS") or "900").strip() or 900)
    response = requests.post(
        f"{base_url}/audio/speech",
        headers=headers,
        json=payload,
        timeout=(10, timeout),
    )
    if not response.ok:
        detail = " ".join(response.text.split())[:400]
        raise ValueError(f"VibeVoice TTS failed with HTTP {response.status_code}: {detail}")
    if not response.content:
        raise ValueError("VibeVoice TTS returned an empty audio response")

    with open(speech_file_path, "wb") as handle:
        handle.write(response.content)

    quality_header = response.headers.get("X-Synth-Quality", "")
    if quality_header:
        parts = quality_header.split("/")
        if len(parts) == 2:
            try:
                avg_logprob, no_speech_prob = float(parts[0]), float(parts[1])
                if avg_logprob < -0.8 or no_speech_prob > 0.6:
                    logging.warning(
                        f"Possible garbled output for {speech_file_path}: "
                        f"avg_logprob={avg_logprob:.2f} no_speech_prob={no_speech_prob:.2f}"
                    )
            except ValueError:
                pass
    return None


def tts_elevenlabs(text: str, speech_file_path: str, clean_mp3 = True) -> str:
    import os
    import subprocess

    from dotenv import load_dotenv
    from elevenlabs import VoiceSettings
    from elevenlabs.client import ElevenLabs

    load_dotenv()

    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

    if not ELEVENLABS_API_KEY:
        raise ValueError("ELEVENLABS_API_KEY environment variable not set")

    client = ElevenLabs(
        api_key=ELEVENLABS_API_KEY,
    )

    # Calling the text_to_speech conversion API with detailed parameters
    try:
        response = client.text_to_speech.convert(
            voice_id="iP95p4xoKVk53GoZ742B",  # Adam pre-made voice
        optimize_streaming_latency="0",
        output_format="mp3_22050_32",
        text=text,
        model_id="eleven_multilingual_v2",  # use the turbo model for low latency, for other languages use the `eleven_multilingual_v2`
        voice_settings=VoiceSettings(
            stability=0.9,
            similarity_boost=1.0,
            style=0.0,
            use_speaker_boost=True,
            ),
        )
    except Exception as e:
        logging.error(f"Error converting text to speech: {e}")
        return None

    # file_basename - file name without extension
    file_basename = os.path.splitext(speech_file_path)[0]

    # Generating a unique file name for the output MP3 file
    save_file_path = f"{file_basename}.mp3"
    # Writing the audio stream to the file

    with open(save_file_path, "wb") as f:
        for chunk in response:
            if chunk:
                f.write(chunk)

    logging.info(f"A new audio file was saved successfully at {save_file_path}")
    logging.info(f"Converting file to OGG format")
    # Convert MP3 to OGG using ffmpeg without any output and allow overwriting
    try:
        subprocess.run(["ffmpeg", "-y", "-i", save_file_path, f"{speech_file_path}"], capture_output=True)
    except Exception as e:
        logging.error(f"Error converting file to OGG format: {e}")

    # Remove the MP3 file
    if clean_mp3:
        os.remove(save_file_path)

    # Return the path of the saved audio file
    return None


def process_audio_pydub_new(input_path: str, output_path: str):
    from pydub import AudioSegment
    from pydub.effects import compress_dynamic_range

    # Load the input audio file (any format supported by pydub/FFmpeg)
    audio = AudioSegment.from_file(input_path)
    logging.info(f"Loaded audio: duration={len(audio)/1000:.2f} sec, RMS dBFS={audio.dBFS:.2f}")

    # ================================================================
    # Step 1: Filter (simulate FilterCurve)
    #
    # We apply a high-pass filter at 100 Hz as an approximation.
    # ================================================================
    logging.info("Applying high-pass filter at 100 Hz")
    audio = audio.high_pass_filter(100)

    # ================================================================
    # Step 2: Pre-Limiter to tame extreme peaks
    #
    # By applying a dynamic range compression with a high ratio, we
    # reduce the amplitude of transient peaks before normalization.
    # ================================================================
    limiter_threshold = -5.0  # dBFS threshold for limiting
    limiter_ratio = 20.0      # high ratio for near limiting effect
    attack_ms = 1             # very fast attack (ms)
    release_ms = 20           # release time (ms)
    logging.info(f"Applying pre-limiter: threshold={limiter_threshold} dB, ratio={limiter_ratio}, attack={attack_ms} ms, release={release_ms} ms")
    audio = compress_dynamic_range(audio,
                                   threshold=limiter_threshold,
                                   ratio=limiter_ratio,
                                   attack=attack_ms,
                                   release=release_ms)

    # ================================================================
    # Step 3: Loudness Normalization
    #
    # Instead of simply boosting the overall level (which can create
    # new clipping), we measure the current RMS (approximated by dBFS)
    # and adjust the gain to target -20 dBFS.
    # ================================================================
    target_rms = -20.0  # Target overall loudness in dBFS
    current_rms = audio.dBFS
    change_in_dB = target_rms - current_rms
    logging.info(f"Normalizing loudness: current RMS={current_rms:.2f} dB, target RMS={target_rms} dB, applying gain of {change_in_dB:.2f} dB")
    audio = audio.apply_gain(change_in_dB)

    # ================================================================
    # Step 4: Post-Limiter: Peak Check
    #
    # Now check the maximum peak level. If it exceeds -3.5 dB,
    # apply an additional gain reduction so that peaks are capped.
    # ================================================================
    current_peak = audio.max_dBFS
    max_peak_reduction = -4
    if current_peak > max_peak_reduction:
        gain_reduction = max_peak_reduction - current_peak  # This is negative
        logging.info(f"Post-limiter: current peak={current_peak:.2f} dB, reducing gain by {gain_reduction:.2f} dB to cap peaks at {max_peak_reduction:.2f} dB")
        audio = audio.apply_gain(gain_reduction)
    else:
        logging.info(f"Post-limiter: current peak={current_peak:.2f} dB, no additional gain reduction needed")

    # ================================================================
    # Export the processed audio file.
    # The output format is inferred from the file extension.
    # ================================================================
    output_format = output_path.split('.')[-1]
    logging.info(f"Exporting processed audio to '{output_path}' (format: {output_format})")
    audio.export(output_path, format=output_format)
    logging.info("Processing complete.")



def normalize_and_limit_audio(input_path, output_path, target_dBFS=-20.0, limit_dBFS=-3.5, method="pydub_new"):
    if method == "ffmpeg":
        # executing file pd-051-ffmpeg-norm.sh in the same directory
        import os
        import subprocess

        # Get the directory of the currently running Python script
        current_directory = os.path.dirname(os.path.realpath(__file__))

        # Construct the path to the Bash script
        bash_script_path = os.path.join(current_directory, "pd-051-ffmpeg-norm.sh")

        # Your other variables (assuming these are defined elsewhere in your code)
        # input_path, output_path, target_dBFS, limit_dBFS

        # Construct the command
        command = f"{bash_script_path} {input_path} {output_path} {limit_dBFS}"

        # Execute the command
        logging.info(f"normalize_and_limit_audio: [{command = }]")
        process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()

        # Handle the output and errors if necessary
        if error:
            logging.error(f"[{bash_script_path}] error: [{error.decode()}]")
        else:
            logging.info(f"[{bash_script_path}] output: [{output.decode()}]")



        # import subprocess
        # command = f"./pd-051-ffmpeg-norm.sh {input_path} {output_path} {target_dBFS} {limit_dBFS}"
        # process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        # output, error = process.communicate()

        
    elif method == "pydub":
        from pydub import AudioSegment
        from pydub.utils import mediainfo
        import math

        audio = AudioSegment.from_file(input_path)

        # Normalize the audio to the target dBFS
        change_in_dBFS = target_dBFS - audio.dBFS
        normalized_audio = audio.apply_gain(change_in_dBFS)

        # Get peak amplitude in dBFS
        peak_amplitude_dBFS = 20 * math.log10(normalized_audio.max / normalized_audio.max_possible_amplitude)

        # Apply limiting if necessary
        if peak_amplitude_dBFS > limit_dBFS:
            limiting_gain = limit_dBFS - peak_amplitude_dBFS
            limited_audio = normalized_audio.apply_gain(limiting_gain)
        else:
            limited_audio = normalized_audio

        # Export the processed audio
        # print (mediainfo(input_path))
        limited_audio.export(output_path, format=mediainfo(input_path)['format_name'])
    elif method == "pydub_new":
        process_audio_pydub_new(input_path, output_path)
    else:
        raise ValueError("Unknown method", method)
    

    return None




def tts(episode, temp_dir, path):
    transName = get_params("improved_text_key")
    sleep_time_tts = get_params("sleep_time_tts")
    voice = get_voice_name(path)

    # add progress bar
    from tqdm import tqdm

    for chunk in tqdm(episode):
        # define file name, 
        #   if there is end time, use start-end, e.g. 0122-0255
        #   otherwise use only start time, e.g. 0122

        audio_file_name = f'{chunk["start"]}-{chunk["end"]}' if "end" in chunk else f'{chunk["start"]}'
        audio_file_path = f'{temp_dir}/{audio_file_name}.ogg'
        # print(audio_file_path)


        if chunk[transName]:
            generate_openai_tts(path = path, text=chunk[transName], speech_file_path= audio_file_path, voice=voice)
        else:
            logging.info(f"Empty text in chunk {chunk['start']}-{chunk['end']}")
        # we will not normalize here - we will normilize the final file
        # normalize_and_limit_audio(audio_file_path, audio_file_path)
    
        import time
        time.sleep(int(sleep_time_tts))

    logging.info(f"Generated audio saved in  {temp_dir}")
    return None


def  change_tempo (outfile, speedup):
    # from https://pyrubberband.readthedocs.io/en/stable/
    import soundfile as sf
    import pyrubberband as pyrb
    y, sr = sf.read(outfile)
    # Play back at double speed
    y_stretch = pyrb.time_stretch(y, sr, speedup)
    sf.write (outfile, y_stretch, sr)




def update_tts_audio(path, path_synthesis, custom_speedup=None, tempo_by_filename=None):
    """Re-encode every staged clip, applying tempo per file.

    `tempo_by_filename` lets one assembly mix generated and recorded chunks:
    speeding up a recording the user made themselves is never wanted, while
    generated speech still gets the project tempo.
    """
    import os
    # download original file
    local_file_orig = get_local_path_with_download(path)

    temp_dir_combine = create_timestamped_directory(local_file_orig)

    # get list of all files in a directory
    src_format = "ogg" ## change to params later
    import glob
    src_trans_files = sorted(glob.glob(f"{path_synthesis}/*.{src_format}" ))


    default_speedup = custom_speedup or get_params("speedup_value")
    if default_speedup != 1.0 :
        logging.info (f"Changing the speed on {str ( (default_speedup - 1) * 100 )}%. Consider 'Change Tempo' effect in Audacity for better quality..")

    for infile in src_trans_files:
        if (os.path.getsize(infile) ==0 ): continue
        infile_basename = os.path.basename(infile)
        speedup = (tempo_by_filename or {}).get(infile_basename, default_speedup)
        outfile = f"{temp_dir_combine}/{infile_basename}"  ## 
        logging.info (f"Updating {infile} and saving to {outfile} at tempo {speedup}")


        if speedup != 1.0 :
            # logging.info (f"Changing the speed on {str ( (speedup - 1) * 100 )}%. Consider 'Change Tempo' effect in Audacity for better quality..")
            # audio = AudioSegment.from_file(outfile, "wav") 
            # audio = speed_change(audio, speedup)
            # audio.export(outfile, format="wav")
            # from https://stackoverflow.com/questions/43408833/how-to-increase-decrease-playback-speed-on-wav-file
            # need to fix it for pydub
            # slow_sound = speed_change(sound, 0.75)
            # need to run ffmpeg again
            # second, remove smaller pauses with ffmpeg
            speedup_option = "ffmpeg"

            if speedup_option == "pyrubberband":
                ## not working in container
                logging.info(f"INFO: saving outfile with ffmpeg {outfile}")
                command = f"ffmpeg -hide_banner -loglevel error -i {infile} {outfile}"
                # command = "ffmpeg -hide_banner -loglevel error -i {} -af {} {}".format(tmp_file, ffmpeg_params, outfile)
                import subprocess
                process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
                output, error = process.communicate()

                change_tempo (outfile, speedup)

            elif speedup_option == "ffmpeg":
                logging.info(f"INFO: saving outfile with ffmpeg {outfile}")
                command = f"ffmpeg -hide_banner -loglevel error -i {infile} -filter:a atempo={speedup} {outfile}"
                # command = "ffmpeg -hide_banner -loglevel error -i {} -af {} {}".format(tmp_file, ffmpeg_params, outfile)
                import subprocess
                process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
                output, error = process.communicate()
        else:
            # copy infile to outfile using cp
            logging.info(f"INFO: saving outfile with ffmpeg {outfile}")
            command = f"ffmpeg -hide_banner -loglevel error -i {infile} {outfile}"
            # command = f"cp {infile} {outfile}"
            import subprocess
            process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
            output, error = process.communicate()




    



    return temp_dir_combine

# def normalize_and_limit_single_audio(input_path, output_path, target_dBFS=-20.0, limit_dBFS=-3.5):

def valid_tts_filename_format(s: str):
    """
    Validates if the string matches the formats '0000-0000' or '0000'.
    Raises an exception if the string does not match.
    
    Args:
    s (str): The string to validate.

    Raises:
    Error: If the string does not match the required formats.
    """
    # Define regex patterns for matching
    pattern1 = r'^\d{4}-\d{4}$'  # Matches '0000-0000'
    pattern2 = r'^\d{4}$'        # Matches '0000'
    pattern3 = r'^\d{6}-\d{6}$'  # Matches '000000-000000'

    # Check if the string matches either of the regex patterns
    if not (re.match(pattern1, s) or re.match(pattern2, s) or re.match(pattern3, s)):
        logging.error(f"valid_tts_filename_format: Provided string '{s}' does not match the required formats '0000-0000' or '0000' or '000000-000000'.")
    
        return False
    return True

def extract_start_time_str_from_filename(filename: str):
    """
    Extracts the start time string from the filename.
    anything from start until the first '-' or end of the string
    """
    if '-' in filename:
        result = filename[:filename.find('-')]
    else:
        result = filename
    return result

def get_segment_end_seconds(filename, filename_next):
    """
    Get the end time of the current segment in seconds.
    If pattern is 0000-0000 or 000000-000000, 
    then the end of the current segment is in the name of the current file
    otherwise, it is the start of the next segment
    """
    try:
        if (re.match(r'^\d{4,6}-\d{4,6}$', filename)):
            segment_end_str = filename[filename.find('-')+1:]
            segment_end = convert_hhmmss_mmss_to_seconds(segment_end_str)
        else:
            segment_end = convert_hhmmss_mmss_to_seconds(filename_next)
    except Exception as e:
        logging.error(f"get_segment_end_seconds: Error processing segment {filename} or {filename_next}: {e}")
        return None
    return segment_end


def combinne_with_original_audio(path, path_combine, shift_seconds=None):
    import os

    logging.info(f"Combining original [{path}] and updated audio in [{path_combine}]")


    #########################################
    ################### settings
    # how long the delay should be after the speaker starts the translation
    if shift_seconds is None:
        shift = 1.5 * 1000
    else:
        shift = shift_seconds * 1000
    
    # fading time for voice inbetween translations
    fade_time = int(1.2*1000)
    
    # how quiter wiil the original be when translation is played
    quiter_orig_value = 80

    # added shifted in aws-01-parse 
    # see if 1 sec sounds better than 2 which was previously (cahnged on 2021-07-22)
    # not actual any longer
    added_shift = 0

    # segment alignment
    # "s" - start of the segment
    # "e" - end of the segment
    segment_alignment = "e"

    # if we need to create silence instead of original audio
    # for example if we need to create just voiceover
    # silence = True
    silence = False

    # if we need to use default first segment start
    use_default_first_segment_start = False

    # for the first segment we force to start from the first_segment_start
    # changed as we have new whisper approach with words timestamps
    # used only if use_default_first_segment_start = True
    first_segment_start = 3*1000

    ############################


    # get list of all OGG/MP3 files in a directory
    src_format = "ogg" ## TODO change to params later
    src_format_len = len(src_format)
    import glob
    src_trans_files = sorted(glob.glob( f"{path_combine}/*.{src_format}"))

    # download original file
    local_file_orig = get_local_path_with_download(path)
    logging.info(f"Path used [{local_file_orig =}]")

    from pydub import AudioSegment
    original = AudioSegment.from_mp3(local_file_orig) 
    if silence:
        logging.info("Creating silence instead of original audio")
        original = AudioSegment.silent(duration=len(original))

    # initiate result with the first file
    longMP3 = AudioSegment.silent(duration=0)

    logging.info(f"combinne_with_original_audio: Processing {src_trans_files}")

    for i in range(0,len(src_trans_files)):
        logging.info(f"combinne_with_original_audio: Processing segment {i} of {len(src_trans_files)}")
        file = src_trans_files[i]
        file_basename = os.path.basename(file)
        file_basename_no_ext = file_basename[:-src_format_len-1]
        ## check if file_basename_no_ext has format 0000-0000 or 0000
        if not valid_tts_filename_format(file_basename_no_ext):
            logging.error(f"combinne_with_original_audio: Invalid tts filename format: {file_basename_no_ext}")
            continue

        if (os.path.getsize(file) ==0 ): continue
        #print (file)
        curr_duration = len(longMP3)
        # segment start time in milliseconds (mm * 60 + ss) * 1000, where mm - minutes, ss - seconds are from the file name, first 4 or 6 chars
        segment_start_str = extract_start_time_str_from_filename(file_basename_no_ext)
        segment_start = convert_hhmmss_mmss_to_seconds(segment_start_str) * 1000 + shift
        segment_length = len( AudioSegment.from_file(file, src_format))

        # moving segment so that it ends when the next segment starts
        if ( segment_alignment in ("e", "end")):
            if ( i >= 0 and i < len(src_trans_files) - 1  ) :
                file_next = src_trans_files[i + 1]
                file_next_basename_no_ext = os.path.basename(file_next)[:-src_format_len-1]
                # print(file_next)

                # if filename is in format 0000-0000, 
                #   define segment_end as from the current file name
                # otherwise, 
                #   define segment_end as from the next file name
                segment_end = get_segment_end_seconds(file_basename_no_ext, file_next_basename_no_ext) * 1000

                segment_start = segment_end - segment_length

                if ( i == 0 ):
                    # first segment start
                    if use_default_first_segment_start:
                        # for the first segment we force to start from the first_segment_start
                        segment_start = first_segment_start
                    else:
                        # for the first segment we use time from the file name if it is longer than first_segment_start, otherwise we use first_segment_start
                        segment_start_detected = (int(file_basename_no_ext[0:2])*60 + int(file_basename_no_ext[2:4]))*1000
                        segment_start = max( segment_start_detected + first_segment_start, first_segment_start, segment_start)


        elif not ( segment_alignment in ("s", "start")):
            logging.error("combinne_with_original_audio: Error! Not correct alignment argument -s")
            raise ValueError("combinne_with_original_audio: Error! Not correct alignment argument -s")



        if (segment_start > curr_duration) :
            silence_duration = segment_start - curr_duration
            ## if this is the first segment, we force to start from the first segment

            if (silence_duration < 2*fade_time):
                # If no valume rais in cort silence
                # longMP3 = longMP3 + AudioSegment.silent(duration=silence_duration)
                #
                # if we raise volume during silence
                fade_time_tmp = int (silence_duration / 2 )
                fill_orig_tmp = original[curr_duration:segment_start].fade_in(fade_time_tmp).fade_out(fade_time_tmp)
                ### reduce volume for short inserts:
                ### quiter_orig_value when fade_time_tmp = 0
                ### 0 when fade_time_tmp = fade_time
                fill_orig_tmp = fill_orig_tmp - (fade_time - fade_time_tmp) / fade_time * quiter_orig_value
                longMP3 = longMP3 + fill_orig_tmp

            else:
                longMP3 = longMP3 + original[curr_duration:segment_start].fade_in(fade_time).fade_out(fade_time)
        else:
            silence_duration = 0
        
        #print (silence_duration/1000, " sec delay")
        # logging.info (f"segment: {}:{}, file: {}, delay: {} sec".format((file[-(src_format_len+5):-(src_format_len+3)]), 
        # (file[-(src_format_len+3):-(src_format_len+1)]), 
        # file, silence_duration/1000) )
        logging.info(f"segment [{file_basename_no_ext}], [{curr_duration = }], [{silence_duration = }], [{segment_start = }], [{segment_length = }]")
        if (src_format == "mp3"):
            logging.info(f"combinne_with_original_audio: Processing segment {i} of {len(src_trans_files)}: src_format == mp3")
            new_audio = AudioSegment.from_mp3(file)
        elif (src_format == "ogg"):
            logging.info(f"combinne_with_original_audio: Processing segment {i} of {len(src_trans_files)}: src_format == ogg")
            try:
                new_audio = AudioSegment.from_ogg(file)
            except Exception as e:
                logging.error(f"combinne_with_original_audio: Error processing segment {i} of {len(src_trans_files)}: src_format == ogg: {e}")
                raise e
        elif (src_format == "wav"):
            logging.info(f"combinne_with_original_audio: Processing segment {i} of {len(src_trans_files)}: src_format == wav")
            new_audio = AudioSegment.from_file(file, "wav")
        else:
            raise ValueError("Unknown file format", src_format)
        longMP3 = longMP3 + new_audio

    ## adding original segment at the end
    logging.info(f"combinne_with_original_audio: Adding original segment at the end")
    curr_duration = len(longMP3)
    segment_start = len(original)
    if (segment_start > curr_duration) :
        silence_duration = segment_start - curr_duration
        #longMP3 = longMP3 + AudioSegment.silent(duration=silence_duration)
        #longMP3 = longMP3 + original[int(segment_start):int(curr_duration)].fade_in(fade_time).fade_out(fade_time)
        if (silence_duration < 2*fade_time):
            # If no valume rais in cort silence
            # longMP3 = longMP3 + AudioSegment.silent(duration=silence_duration)
            #
            # if we raise volume during silence
            fade_time_tmp = int (silence_duration / 2)
            longMP3 = longMP3 + original[curr_duration:segment_start].fade_in(fade_time_tmp)
        else:
            longMP3 = longMP3 + original[curr_duration:segment_start].fade_in(fade_time)
    else:
        silence_duration = 0

    ## handle the case when original is shorter than translation
    ## we need to add silence to the end of the original
    logging.info(f"combinne_with_original_audio: handle the case when original is shorter than translation: len(original) = {len(original)} < len(longMP3) = {len(longMP3)}")
    if (len(original) < len(longMP3)):
        logging.info(f"combinne_with_original_audio: Adding silence to the end of the original")
        silence_duration = len(longMP3) - len(original)
        original = original + AudioSegment.silent(duration=silence_duration)
        logging.info(f"combinne_with_original_audio: len(original) = {len(original)} == len(longMP3) = {len(longMP3)}")
    else:
        logging.info(f"combinne_with_original_audio: len(original) = {len(original)} == len(longMP3) = {len(longMP3)}")

    # adding original audio with reduced volume
    logging.info(f"Reducing volume of the original audio by {quiter_orig_value} dB")
    original_quiter = original - quiter_orig_value
    logging.info(f"Adding original audio with reduced volume")
    mixed = original_quiter.overlay(longMP3)

    # if we need to add timestamp to the tmp file name
    # current date and time
    logging.info(f"Creating temp filename with timestamp")
    from datetime import datetime as dt
    now = dt.now() 
    tmp_outFile = local_file_orig[:-4] + "-" + now.strftime("%Y-%m-%d-%H%M") + ".mp3"

    logging.info(f"Saving combined audio to [{tmp_outFile}]")
    mixed.export(tmp_outFile, format="mp3")

    tmp_outFile_normalized = f"{tmp_outFile}_normalized.mp3"
    logging.info(f"Skip the whole file normalization, just copy it to [{tmp_outFile_normalized}]")
    copy_or_upload(tmp_outFile, tmp_outFile_normalized)
    # logging.info(f"Normalize loudness to [{tmp_outFile_normalized}]")
    # normalize_and_limit_audio(tmp_outFile, tmp_outFile_normalized)


    ## save to the place where original file is
    path_mp3_voiceover = naming_convention(path, "mp3_voiceover")
    logging.info(f"Copying final audio to [{path_mp3_voiceover}]")
    copy_or_upload(tmp_outFile_normalized, path_mp3_voiceover)


    return None

def list_matching_blobs(container_name, pattern):
    """
    List all blobs in an Azure container matching a given pattern.

    Args:
        container_name (str): The name of the Azure storage container.
        connection_string (str): The connection string for the Azure Blob Storage account.
        pattern (str): The regex pattern to match blob names.

    Returns:
        list: A list of matching blob names.
    """
    # Initialize BlobServiceClient
    AZURE_STORAGE_CONNECTION_STRING = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container_client = blob_service_client.get_container_client(container_name)

    # Compile the regex pattern
    regex = re.compile(pattern)

    # List all blobs and filter using the regex pattern
    logging.info(f"{regex=}, {pattern=}")
    matching_blobs = [blob.name for blob in container_client.list_blobs() if regex.match(blob.name)]
    # matching_blobs = [blob.name for blob in container_client.list_blobs()]

    return matching_blobs

def convert_webm_to_ogg_pydub(path, remove_original = True, ext_in = "webm", ext_out = "ogg"):
    from pydub import AudioSegment
    audio = AudioSegment.from_file(path, ext_in)
    path_out = f"{path[:-len(ext_in)]}{ext_out}"
    audio.export(path_out, format=ext_out)
    if remove_original:
        os.remove(path)
    return path_out

def convert_webm_to_ogg(path, remove_original=True, ext_out="ogg"):
    """
    Converts a .webm file to .ogg format using ffmpeg.

    Parameters:
        path (str): Path to the input .webm file.
        remove_original (bool): If True, removes the original .webm file after conversion.
        ext_out (str): Output file extension (default is 'ogg').

    Returns:
        str: Path to the converted .ogg file.

    Raises:
        RuntimeError: If ffmpeg encounters an error during conversion.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input file not found: {path}")

    if not path.endswith(".webm"):
        raise ValueError(f"Input file must have .webm extension: {path}")

    # Define the output file path
    path_out = f"{os.path.splitext(path)[0]}.{ext_out}"

    # Construct the ffmpeg command
    command = [
        "ffmpeg",
        "-i", path,  # Input file
        "-vn",       # Disable video
        "-acodec", "libvorbis",  # Use Vorbis codec for .ogg
        path_out       # Output file
    ]

    # Run the ffmpeg command
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error("FFmpeg error output:", e.stderr)
        raise RuntimeError(f"Error during conversion: {e.stderr}") from e

    # Remove the original file if required
    if remove_original:
        os.remove(path)

    return path_out


def convert_m4a_to_ogg(path, remove_original=True, ext_out="ogg"):
    """
    Converts a .m4a file to .ogg format using ffmpeg.

    Parameters:
        path (str): Path to the input .m4a file.
        remove_original (bool): If True, removes the original .m4a file after conversion.
        ext_out (str): Output file extension (default is 'ogg').

    Returns:
        str: Path to the converted .ogg file.

    Raises:
        RuntimeError: If ffmpeg encounters an error during conversion.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input file not found: {path}")

    if not path.endswith(".m4a"):
        raise ValueError(f"Input file must have .m4a extension: {path}")

    # Define the output file path
    path_without_ext = os.path.splitext(path)[0]
    path_out = f"{path_without_ext}.{ext_out}"
    # if path_out exists, extract last 2 cahrs from the file name, 
    # convert to int, add 1, convert to str, add .m4a to the end
    if os.path.exists(path_out):
        last_two_chars = path_without_ext[-2:]
        try:
            last_two_chars_int = int(last_two_chars)
        except:
            raise ValueError(f"convert_m4a_to_ogg: last_two_chars is not a number: {last_two_chars=}, {path_out=}")
        # Keep incrementing until we find a non-existing file
        while True:
            last_two_chars_int += 1
            path_without_ext_candidate = f'{path_without_ext[:-2]}{last_two_chars_int:02d}'
            path_out_candidate = f'{path_without_ext_candidate}.{ext_out}'
            if not os.path.exists(path_out_candidate):
                path_without_ext = path_without_ext_candidate
                path_out = path_out_candidate
                break
        logging.info(f"convert_m4a_to_ogg: path_out already exists, renaming to {path_out}")


    # Construct the ffmpeg command
    command = [
        "ffmpeg",
        "-i", path,  # Input file
        "-vn",       # Disable video
        "-acodec", "libvorbis",  # Use Vorbis codec for .ogg
        path_out       # Output file
    ]
    logging.info(f"convert_m4a_to_ogg: ffmpeg command: { ' '.join(command) }")
    # Run the ffmpeg command
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        # subprocess.run(command)
    except subprocess.CalledProcessError as e:
        logging.error("FFmpeg error output:", e.stderr)
        raise RuntimeError(f"Error during conversion: {e.stderr}") from e

    # Remove the original file if required
    if remove_original:
        os.remove(path)

    return path_out



def download_custom_recording_from_storage_account(path, temp_dir):
    # from frontend.shared_functions_frontend import get_data_from_api

    user_id = get_user_id_from_sta_path(path)
    container_name = get_container_name_from_id(user_id)
    base_name = naming_convention(path, "base_name")
    pattern_m4a = r"{base_name}\.\d{{3}}\.m4a".format(base_name=base_name)
    pattern_ogg = r"{base_name}\.\d{{3}}\.ogg".format(base_name=base_name) # ogg is already normalized files with audacity
    files_list_m4a = list_matching_blobs(container_name, pattern_m4a)
    files_list_ogg = list_matching_blobs(container_name, pattern_ogg)

    # if files_list_ogg is empty, use files_list_m4a
    if not files_list_ogg:
        files_list = files_list_m4a
        extension = "m4a"
    else:
        files_list = files_list_ogg
        extension = "ogg"

    # print(f"files_list = {files_list}")
    # read improved file
    improved_file_chunks = json.loads(get_palintext_content(naming_convention(path, "improved")))
    # print(f"improved_file_chunks = {improved_file_chunks}")
    for i,chunk in enumerate(improved_file_chunks):
        chunk_id_str = f"{i:03d}.{extension}"
        for file in files_list:
            if chunk_id_str in file:
                chunk_name_str = f'{chunk.get("start", "")}-{chunk.get("end", "")}'.strip("-")
                local_file_path = f'{temp_dir}/{chunk_name_str}.{extension}'
                sta_file_path = f'{naming_convention(path, "directory")}/{file}'
                # print(f"downloading {sta_file_path =} to {local_file_path = }")
                azure_blob_transfer(sta_file_path, "download", local_file_path)
                if extension == "m4a":
                    local_file_path_ogg = convert_m4a_to_ogg(local_file_path)
                    # print(f"converted {local_file_path} to {local_file_path_ogg}")
                else:
                    local_file_path_ogg = local_file_path

                logging.info(f"download_custom_recording_from_storage_account: converted {sta_file_path =} to {local_file_path_ogg =}")
    return extension


def download_custom_recording_from_storage_account_webm(path, temp_dir):
    # from frontend.shared_functions_frontend import get_data_from_api

    user_id = get_user_id_from_sta_path(path)
    container_name = get_container_name_from_id(user_id)
    base_name = naming_convention(path, "base_name")
    pattern = r"{base_name}\.\d{{3}}\.webm".format(base_name=base_name)
    files_list = list_matching_blobs(container_name, pattern)
    # print(f"files_list = {files_list}")
    # read improved file
    improved_file_chunks = json.loads(get_palintext_content(naming_convention(path, "improved")))
    # print(f"improved_file_chunks = {improved_file_chunks}")
    for i,chunk in enumerate(improved_file_chunks):
        chunk_id_str = f"{i:03d}.webm"
        for file in files_list:
            if chunk_id_str in file:
                local_file_path = f'{temp_dir}/{chunk["start"]}-{chunk["end"]}.webm'
                sta_file_path = f'{naming_convention(path, "directory")}/{file}'
                # print(f"downloading {sta_file_path =} to {local_file_path = }")
                azure_blob_transfer(sta_file_path, "download", local_file_path)
                local_file_path_ogg = convert_webm_to_ogg(local_file_path)
                # print(f"converted {local_file_path} to {local_file_path_ogg}")
                logging.info(f"download_custom_recording_from_storage_account: converted {sta_file_path =} to {local_file_path_ogg =}")
    return "Success"


def process_custom_recording(temp_dir_raw, temp_dir_processed, filetype_downloaded, ext_in = "ogg"):
    src_trans_files = sorted(glob.glob(temp_dir_raw + "/*." + ext_in))
    tmp_file = f"{temp_dir_raw}/tmp.{ext_in}"
    tmp_file_normalized = f"{temp_dir_raw}/tmp_normalized.{ext_in}"
    ffmpeg_params = "silenceremove=stop_periods=-1:stop_duration=0.2:stop_threshold=-40dB"

    for infile in src_trans_files:
        if (os.path.getsize(infile) ==0 ): 
            continue
        outfile = f"{temp_dir_processed}/{os.path.basename(infile)}"

        logging.info (f"process_custom_recording: normalizing loudness and truncating silence in {infile} and saving to {outfile}")

        # first, let's normalize the audio
        # print(f"1. normalize_and_limit_audio({infile=}, {tmp_file_normalized=})")
        if filetype_downloaded == "m4a":
            # downloaded unprocessed m4a files, so we need to normalize and limit the audio
            normalize_and_limit_audio(infile, tmp_file_normalized)
        else:
            # downloaded processed ogg files, so we just copy them
            logging.info(f"process_custom_recording: no need to normalize {infile}, just copying it to {tmp_file_normalized}")
            copy_or_upload(infile, tmp_file_normalized)
        # logging.info("skipping normalization")
        # copy_or_upload(infile, tmp_file_normalized)

        # second, let's remove clicks and long pauses with python script
        # print(f"2. shared_clicks_removal({tmp_file_normalized=}, {tmp_file=})")
        silence_params = 3 # how aggressive is the clicks removal
        command = f"python backend/processing_container/shared_clicks_removal.py {silence_params} {tmp_file_normalized} {tmp_file}"
        logging.info(f"process_custom_recording: removing clicks and long pauses with python script, {command=}")
        process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        # process = subprocess.Popen(command.split())
        output, error = process.communicate()

        # third, remove smaller pauses with ffmpeg
        # print(f"3. ffmpeg -hide_banner -loglevel error -i {tmp_file} -af {ffmpeg_params} {outfile}")
        command = f"ffmpeg -hide_banner -loglevel error -i {tmp_file} -af {ffmpeg_params} {outfile}"
        logging.info(f"process_custom_recording: removing smaller pauses with ffmpeg, {command=}")
        process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        # process = subprocess.Popen(command.split())
        output, error = process.communicate()
    return "Success"


def prepare_custom_recording_dir(path, user_id, file_name):
    temp_dir_raw = create_timestamped_directory( base = f"backend/processing_container/_processing_files_" )
    logging.info(f"prepare_custom_recording_dir, Created custom recording directory [{temp_dir_raw =}]")
    if not file_from_sta(path):
        logging.info(f"prepare_custom_recording_dir, file [{path}] is not from storage account, skipping custom recording preparation")
        return None

    # download custom recording from storage account
    filetype_downloaded = download_custom_recording_from_storage_account(path, temp_dir_raw)
    if filetype_downloaded:
        logging.info(f"prepare_custom_recording_dir, downloaded custom recording from storage account [{path}] to [{temp_dir_raw}], [{filetype_downloaded=}]")
    else:
        logging.info(f"prepare_custom_recording_dir, failed to download custom recording from storage account [{path}] to [{temp_dir_raw}]")
        return None

    temp_dir_processed = create_timestamped_directory( base = f"backend/processing_container/_processing_files_" )
    res = process_custom_recording(temp_dir_raw, temp_dir_processed, filetype_downloaded)

    if res:
        logging.info(f"prepare_custom_recording_dir, processed custom recording from storage account [{path}] to [{temp_dir_processed}]")
        return temp_dir_processed
    else:
        logging.info(f"prepare_custom_recording_dir, failed to process custom recording from storage account [{path}] to [{temp_dir_processed}]")
        return None


# ---------------------------------------------------------------------------
# Per-chunk segment store
#
# Audio belongs to a chunk, not to a pipeline run. Synthesis fills gaps in the
# store, the portal can overwrite any single entry with a recording, and
# assembly reads the store. Nothing downstream cares which produced a chunk.
# ---------------------------------------------------------------------------


def param_bool(all_params, key, default):
    value = all_params.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def resolve_tempo(all_params, key, default):
    value = all_params.get(key)
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        logging.info(f"Invalid {key} value [{value}], using {default}")
        return float(default)


def resolve_project_root(path, path_improved):
    root = portal_project_dir(path)
    if root is not None:
        return Path(root)
    # Azure runs have no portal project folder; keep the store beside the local
    # working copy so one code path covers both deployments.
    return Path(get_local_file_path(path_improved)).parent


def load_transcript(path_improved):
    transcript = json.loads(get_palintext_content(path_improved))
    if not isinstance(transcript, list):
        raise ValueError(f"Improved transcript is not a chunk array: [{path_improved}]")
    return transcript


def text_keys(all_params):
    return (
        str(all_params.get("improved_text_key") or "imp"),
        str(all_params.get("translation_text_key") or "dltrans"),
    )


def run_ffmpeg(args):
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    logging.info(f"Running: {' '.join(command)}")
    subprocess.run(command, check=True)


def parse_chunk_ids(value):
    if not value:
        return set()
    if isinstance(value, str):
        items = [part.strip() for part in value.replace(",", " ").split() if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value if str(part).strip()]
    else:
        return set()
    invalid = [item for item in items if not seg.is_chunk_id(item)]
    if invalid:
        raise ValueError(f"Invalid chunk id(s): {', '.join(invalid)}")
    return set(items)


def chunk_id_for_segment_key(transcript, segment_key):
    for chunk in transcript:
        try:
            basename = seg.staging_basename(chunk)
        except ValueError:
            continue
        if basename == segment_key:
            return chunk["chunk_id"]
    raise ValueError(f"Segment '{segment_key}' not found in improved transcript")


def project_voice_hash(project_params):
    """Hash only the project's own params, never the image defaults.

    The portal API computes the current hash from `config/params.json` and has
    no access to the worker's `parameters.json`. Folding defaults in here gives
    the two different answers for the same chunk, so the editor would report
    everything as stale while the worker correctly treated it as current.
    """
    return seg.voice_hash(project_params)


def sync_audio_blocks(project_root, transcript, all_params, path_improved, current_voice_hash):
    """Mirror the store onto the transcript so every chunk carries its own audio.

    `segments.json` stays authoritative: the transcript is user-editable, so a
    hand-edit must never be able to strand a file.
    """
    index = seg.load_index(project_root)
    improved_key, translation_key = text_keys(all_params)
    for chunk in transcript:
        chunk_id = chunk.get("chunk_id")
        text = seg.chunk_text(chunk, improved_key, translation_key)
        entry = seg.segment_entry(index, chunk_id) if chunk_id else None
        status = seg.segment_status(
            entry,
            text=text,
            current_text_hash=seg.text_hash(text),
            current_voice_hash=current_voice_hash,
            audio_exists=seg.audio_path(project_root, entry) is not None,
        )
        block = seg.audio_block(entry, status)
        if block is None:
            chunk.pop("audio", None)
        else:
            chunk["audio"] = block
    save_json_with_upload(path_improved, transcript)


def retire_orphaned_segments(project_root, transcript):
    """Audio whose chunk is gone is moved aside, not deleted, so an accidental
    chunk delete in the editor stays recoverable."""
    index = seg.load_index(project_root)
    live = {chunk.get("chunk_id") for chunk in transcript}
    orphans = [chunk_id for chunk_id in list(index.get("segments", {})) if chunk_id not in live]
    if not orphans:
        return
    target = seg.orphan_dir(project_root)
    target.mkdir(parents=True, exist_ok=True)
    for chunk_id in orphans:
        entry = index["segments"].pop(chunk_id)
        for key in ("file", "raw_file"):
            relative = entry.get(key)
            if not relative:
                continue
            source = seg.store_dir(project_root) / relative
            if source.exists():
                shutil.move(str(source), str(target / source.name))
        logging.info(f"Retired orphaned segment [{chunk_id}]; its chunk is no longer in the transcript")
    seg.save_index(project_root, index)


# A de-click that keeps less than this fraction of the take has misread it as
# non-speech rather than trimmed pauses from it.
DECLICK_MIN_RETAINED_FRACTION = 0.25


def wav_frame_count(path):
    import wave

    try:
        with wave.open(str(path)) as handle:
            return handle.getnframes()
    except Exception:
        return None


def remove_clicks(infile, outfile):
    """Legacy VAD de-click, returning True when its output is usable.

    `sound_prep()` rewrites its input in place, so it is handed a scratch copy.
    Failures are tolerated rather than fatal: the ffmpeg pause trim that follows
    still runs, and a recording is worth keeping un-declicked.
    """
    scratch = outfile.parent / "declick-input.wav"
    shutil.copyfile(infile, scratch)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared_clicks_removal.py")
    result = subprocess.run(
        [sys.executable, script, "3", str(scratch), str(outfile)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not outfile.exists():
        detail = " ".join((result.stderr or "").split())[:300]
        logging.warning(f"Click removal failed, keeping the audio as-is: {detail}")
        return False

    # An all-silence verdict writes a header-only wav, which is a non-zero file
    # and would otherwise pass as a valid, silent segment.
    before, after = wav_frame_count(infile), wav_frame_count(outfile)
    if not after:
        logging.warning("Click removal found no speech at all, keeping the audio as-is")
        return False
    if before and after < before * DECLICK_MIN_RETAINED_FRACTION:
        logging.warning(
            f"Click removal kept only {after / before:.0%} of the audio, "
            "which looks like a misread rather than pause trimming; keeping the audio as-is"
        )
        return False
    if before:
        logging.info(f"Click removal kept {after / before:.0%} of the audio")
    return True


def convert_recording_to_canonical(infile, outfile, cleanup=True):
    """Browser upload to canonical ogg, via the legacy recording cleanup chain."""
    work_dir = outfile.parent / f".ingest-{outfile.stem}"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        staged = work_dir / "prepared.wav"
        # webm/m4a/mp4 in, and the de-click step needs mono 16-bit 48 kHz.
        run_ffmpeg(["-i", str(infile), "-ac", "1", "-ar", "48000", "-sample_fmt", "s16", str(staged)])

        if cleanup:
            normalized = work_dir / "normalized.wav"
            normalize_and_limit_audio(str(staged), str(normalized))
            staged = normalized
            declicked = work_dir / "declicked.wav"
            if remove_clicks(staged, declicked):
                staged = declicked

        args = ["-i", str(staged)]
        if cleanup:
            args += ["-af", "silenceremove=stop_periods=-1:stop_duration=0.2:stop_threshold=-40dB"]
        args += ["-c:a", "libvorbis", str(outfile)]
        run_ffmpeg(args)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def ingest_pending_recordings(project_root, transcript, all_params):
    """Convert browser uploads the portal parked in `raw/`.

    The portal API image has no ffmpeg, so it stores the upload untouched and
    leaves the conversion to the worker.
    """
    index = seg.load_index(project_root)
    pending = [
        (chunk_id, entry)
        for chunk_id, entry in index.get("segments", {}).items()
        if entry.get("status") == seg.STATUS_PENDING_INGEST and entry.get("raw_file")
    ]
    if not pending:
        return
    cleanup = param_bool(all_params, "recording_cleanup", True)
    store = seg.store_dir(project_root)
    logging.info(f"Ingesting {len(pending)} uploaded recording(s), cleanup={cleanup}")
    for chunk_id, entry in pending:
        raw = store / entry["raw_file"]
        outfile = store / f"{chunk_id}.{seg.CANONICAL_EXTENSION}"
        if not raw.exists() or raw.stat().st_size == 0:
            entry["status"] = seg.STATUS_FAILED
            entry["error"] = "Uploaded recording is missing or empty"
            continue
        try:
            convert_recording_to_canonical(raw, outfile, cleanup=cleanup)
        except Exception as exc:
            outfile.unlink(missing_ok=True)
            entry["status"] = seg.STATUS_FAILED
            entry["error"] = f"Recording ingest failed: {exc}"[:300]
            logging.error(f"Recording ingest failed for [{chunk_id}]: {exc}")
            continue
        entry.update(
            {
                "file": outfile.name,
                "source": seg.SOURCE_RECORDING,
                "status": seg.STATUS_READY,
                "duration_ms": seg.probe_duration_ms(outfile),
                "bytes": outfile.stat().st_size,
                "created_at": seg.now_iso(),
            }
        )
        entry.pop("error", None)
        logging.info(f"Ingested recording for [{chunk_id}] -> {outfile.name}")
    seg.save_index(project_root, index)


def extract_zip_safely(archive_path, destination):
    destination_resolved = Path(destination).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (Path(destination) / member.filename).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise ValueError(f"Unsafe path in recordings archive: {member.filename}")
        archive.extractall(destination)


def match_files_to_chunks(files, transcript):
    """Map loose recording files onto chunks the way the legacy import did:
    by `start-end`, by `start`, by chunk id, then by position."""
    by_stem = {}
    for item in files:
        by_stem.setdefault(re.sub(r"[^0-9a-zA-Z-]+", "-", item.stem).strip("-").lower(), item)
    ordered = sorted(files)
    matched = {}
    for position, chunk in enumerate(transcript):
        chunk_id = chunk.get("chunk_id")
        if not chunk_id:
            continue
        candidates = []
        try:
            candidates.append(seg.staging_basename(chunk).lower())
        except ValueError:
            pass
        candidates += [str(chunk.get("start") or "").lower(), chunk_id.lower(), f"{position:03d}", str(position)]
        found = next((by_stem[key] for key in candidates if key in by_stem), None)
        if found is None and len(ordered) == len(transcript):
            found = ordered[position]
        if found is not None:
            matched[chunk_id] = found
    return matched


def register_raw_recording(project_root, index, chunk_id, source_file, text):
    raw_target = seg.raw_dir(project_root)
    raw_target.mkdir(parents=True, exist_ok=True)
    extension = source_file.suffix.lstrip(".").lower() or "ogg"
    destination = raw_target / f"{chunk_id}.{extension}"
    shutil.copyfile(source_file, destination)
    index.setdefault("segments", {})[chunk_id] = {
        "raw_file": f"raw/{destination.name}",
        "source": seg.SOURCE_RECORDING,
        "status": seg.STATUS_PENDING_INGEST,
        "text_hash": seg.text_hash(text),
        "bytes": destination.stat().st_size,
        "created_at": seg.now_iso(),
    }


def import_recordings_zip(project_root, transcript, all_params):
    """Bulk import for the project-creation zip upload. Runs once per archive."""
    relative = str(all_params.get("custom_recordings_zip") or "").strip()
    if not relative:
        return
    archive = Path(project_root) / relative
    if not archive.exists():
        logging.info(f"custom_recordings_zip [{archive}] not found, skipping bulk import")
        return
    index = seg.load_index(project_root)
    stat = archive.stat()
    marker = f"{archive.name}:{int(stat.st_mtime)}:{stat.st_size}"
    if index.get("imported_zip") == marker:
        return

    extract_dir = seg.store_dir(project_root) / ".zip-import"
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        extract_zip_safely(archive, extract_dir)
        files = [
            item
            for item in sorted(extract_dir.rglob("*"))
            if item.is_file() and item.suffix.lstrip(".").lower() in seg.RAW_UPLOAD_EXTENSIONS
        ]
        if not files:
            logging.warning(f"No supported recordings found in [{archive}]")
            return
        improved_key, translation_key = text_keys(all_params)
        matched = match_files_to_chunks(files, transcript)
        for chunk in transcript:
            chunk_id = chunk.get("chunk_id")
            source_file = matched.get(chunk_id)
            if source_file is None:
                continue
            register_raw_recording(
                project_root, index, chunk_id, source_file,
                seg.chunk_text(chunk, improved_key, translation_key),
            )
        index["imported_zip"] = marker
        seg.save_index(project_root, index)
        logging.info(f"Imported {len(matched)} recording(s) from [{archive.name}] into the segment store")
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def import_existing_audio_dir(
    project_root, transcript, existing_dir, all_params, current_voice_hash, source=seg.SOURCE_TTS
):
    """Adopt a pre-store directory of `<start>-<end>.ogg` clips into the store.

    Covers the deprecated `--dir` flag and the Azure custom-recording download,
    both of which predate the store and name files after timings.
    """
    directory = Path(existing_dir)
    if not directory.is_dir():
        logging.warning(f"Cannot import audio from [{existing_dir}]: not a directory")
        return
    files = [item for item in sorted(directory.glob("*")) if item.is_file() and item.stat().st_size > 0]
    if not files:
        logging.warning(f"No audio files to import from [{existing_dir}]")
        return
    index = seg.load_index(project_root)
    store = seg.store_dir(project_root)
    store.mkdir(parents=True, exist_ok=True)
    improved_key, translation_key = text_keys(all_params)
    matched = match_files_to_chunks(files, transcript)
    for chunk in transcript:
        chunk_id = chunk.get("chunk_id")
        source_file = matched.get(chunk_id)
        if source_file is None:
            continue
        text = seg.chunk_text(chunk, improved_key, translation_key)
        if source == seg.SOURCE_RECORDING:
            register_raw_recording(project_root, index, chunk_id, source_file, text)
            continue
        destination = store / f"{chunk_id}.{seg.CANONICAL_EXTENSION}"
        shutil.copyfile(source_file, destination)
        index.setdefault("segments", {})[chunk_id] = {
            "file": destination.name,
            "source": seg.SOURCE_TTS,
            "engine": str(all_params.get("tts_api") or "openai").lower(),
            "voice": all_params.get("voice"),
            "duration_ms": seg.probe_duration_ms(destination),
            "bytes": destination.stat().st_size,
            # Adopting a file asserts it matches the transcript as it stands.
            "text_hash": seg.text_hash(text),
            "voice_hash": current_voice_hash,
            "created_at": seg.now_iso(),
            "status": seg.STATUS_READY,
        }
    seg.save_index(project_root, index)
    logging.info(f"Imported {len(matched)} clip(s) from [{existing_dir}] into the segment store")


def failed_segment_entry(store, previous, tts_api, exc):
    """Record the failure without discarding a usable earlier take.

    If the chunk still has audio, the previous status is kept so the build can
    use it; the attached error is what tells the editor the retry did not work.
    """
    entry = dict(previous or {})
    entry["error"] = str(exc)[:300]
    entry["failed_at"] = seg.now_iso()
    existing = entry.get("file")
    if not existing or not (store / existing).exists() or (store / existing).stat().st_size == 0:
        entry.update({"source": seg.SOURCE_TTS, "engine": tts_api, "status": seg.STATUS_FAILED})
    return entry


def synthesize_missing_segments(
    path, project_root, transcript, all_params, current_voice_hash, forced_ids=None
):
    """Generate audio for chunks that need it, one chunk at a time.

    Without `forced_ids` this is a gap filler: it skips anything already current
    and never touches a recording. With `forced_ids` it regenerates exactly those
    chunks, which is what the editor's per-chunk regenerate button sends.
    """
    import time

    improved_key, translation_key = text_keys(all_params)
    voice = get_voice_name(path)
    tts_api = str(all_params.get("tts_api") or "openai").lower()
    model = all_params.get("vibevoice_model") if tts_api == "vibevoice" else all_params.get("openai_model_tts")
    sleep_time = 0.0 if tts_api == "vibevoice" else float(all_params.get("sleep_time_tts") or 0)

    store = seg.store_dir(project_root)
    store.mkdir(parents=True, exist_ok=True)
    index = seg.load_index(project_root)

    if forced_ids:
        known = {chunk.get("chunk_id") for chunk in transcript}
        unknown = sorted(forced_ids - known)
        if unknown:
            raise ValueError(f"Unknown chunk id(s): {', '.join(unknown)}")

    targets = []
    for chunk in transcript:
        chunk_id = chunk.get("chunk_id")
        text = seg.chunk_text(chunk, improved_key, translation_key)
        if not text:
            continue
        if forced_ids:
            if chunk_id in forced_ids:
                targets.append((chunk_id, text))
            continue
        entry = seg.segment_entry(index, chunk_id)
        status = seg.segment_status(
            entry,
            text=text,
            current_text_hash=seg.text_hash(text),
            current_voice_hash=current_voice_hash,
            audio_exists=seg.audio_path(project_root, entry) is not None,
        )
        if seg.needs_synthesis(status, entry):
            targets.append((chunk_id, text))

    logging.info(
        f"Synthesizing {len(targets)} of {len(transcript)} chunk(s) with [{tts_api}], voice [{voice}]"
    )
    if not targets:
        return

    from tqdm import tqdm

    failures = []
    for chunk_id, text in tqdm(targets):
        outfile = store / f"{chunk_id}.{seg.CANONICAL_EXTENSION}"
        # Synthesize aside and move into place, so a failed regeneration cannot
        # destroy or truncate the take that is already there.
        pending = store / f".{chunk_id}.partial.{seg.CANONICAL_EXTENSION}"
        pending.unlink(missing_ok=True)
        try:
            generate_openai_tts(path=path, text=text, speech_file_path=str(pending), voice=voice)
            if not pending.exists() or pending.stat().st_size == 0:
                raise ValueError("TTS produced no audio")
        except Exception as exc:
            pending.unlink(missing_ok=True)
            failures.append(chunk_id)
            index.setdefault("segments", {})[chunk_id] = failed_segment_entry(
                store, seg.segment_entry(index, chunk_id), tts_api, exc
            )
            seg.save_index(project_root, index)
            logging.error(f"TTS failed for chunk [{chunk_id}]: {exc}")
            continue
        os.replace(pending, outfile)
        index.setdefault("segments", {})[chunk_id] = {
            "file": outfile.name,
            "source": seg.SOURCE_TTS,
            "engine": tts_api,
            "voice": voice,
            "model": model,
            "duration_ms": seg.probe_duration_ms(outfile),
            "bytes": outfile.stat().st_size,
            "text_hash": seg.text_hash(text),
            "voice_hash": current_voice_hash,
            "created_at": seg.now_iso(),
            "status": seg.STATUS_READY,
        }
        # The index is the resume point, so it is written per chunk: a crash
        # costs at most the chunk in flight, never the whole run.
        seg.save_index(project_root, index)
        if sleep_time:
            time.sleep(sleep_time)

    if failures:
        raise RuntimeError(f"TTS failed for {len(failures)} chunk(s): {', '.join(failures[:8])}")


# A hair of silence so two merged lines do not sound spliced together.
MERGED_CHUNK_GAP_MS = 150
# Named explicitly because the default encoder for the ogg container varies by
# ffmpeg build: the worker image picks libvorbis, but a Homebrew build with no
# libvorbis silently writes flac instead.
CANONICAL_AUDIO_CODEC = "libvorbis"


def concat_audio(paths, destination):
    """Join clips that share one timing slot into a single staged file.

    Timing keys have one-second resolution, so a fast exchange between speakers
    can put two chunks in the same second. The legacy assembler globs by
    filename, so writing both to one name silently dropped the first; joining
    them keeps both lines in the right place.
    """
    from pydub import AudioSegment

    combined = AudioSegment.empty()
    for index, path in enumerate(paths):
        if index:
            combined += AudioSegment.silent(duration=MERGED_CHUNK_GAP_MS)
        combined += AudioSegment.from_file(path)
    combined.export(str(destination), format=seg.CANONICAL_EXTENSION, codec=CANONICAL_AUDIO_CODEC)


def stage_segments_for_build(project_root, transcript, all_params, current_voice_hash):
    """Copy the store into a scratch directory under the timing-based filenames
    the legacy assembler globs for. Retiming a chunk is then just a rename."""
    index = seg.load_index(project_root)
    improved_key, translation_key = text_keys(all_params)
    tts_tempo = resolve_tempo(all_params, "voiceover_tempo", get_params("speedup_value"))
    recording_tempo = resolve_tempo(all_params, "recording_tempo", 1.0)

    staging = Path(create_timestamped_directory(str(seg.store_dir(project_root) / ".staging")))
    tempo_by_filename = {}
    # basename -> [(chunk_id, audio path, is_recording)], because more than one
    # chunk can land in the same timing slot.
    claimed = {}
    order = []
    merged = []
    skipped = []
    stale = []

    for chunk in transcript:
        chunk_id = chunk.get("chunk_id")
        text = seg.chunk_text(chunk, improved_key, translation_key)
        entry = seg.segment_entry(index, chunk_id) if chunk_id else None
        resolved = seg.audio_path(project_root, entry)
        status = seg.segment_status(
            entry,
            text=text,
            current_text_hash=seg.text_hash(text),
            current_voice_hash=current_voice_hash,
            audio_exists=resolved is not None,
        )
        if status == seg.STATUS_SKIPPED:
            continue
        if resolved is None or status in (seg.STATUS_MISSING, seg.STATUS_FAILED, seg.STATUS_PENDING_INGEST):
            skipped.append(f"{chunk_id} ({status})")
            continue
        if status == seg.STATUS_STALE:
            # Outdated audio still beats a silent hole in the mix, but the user
            # should not have to diff the result to find out it happened.
            stale.append(chunk_id)
        basename = seg.staging_basename(chunk)
        is_recording = (entry or {}).get("source") == seg.SOURCE_RECORDING
        if basename not in claimed:
            claimed[basename] = []
            order.append(basename)
        claimed[basename].append((chunk_id, resolved, is_recording))

    for basename in order:
        members = claimed[basename]
        destination = staging / f"{basename}.{seg.CANONICAL_EXTENSION}"
        if len(members) == 1:
            shutil.copyfile(members[0][1], destination)
        else:
            merged.append(f"{basename} <- {', '.join(member[0] for member in members)}")
            concat_audio([member[1] for member in members], destination)
        # A merged clip holding any recording keeps tempo 1.0: speeding up the
        # user's own voice to match generated speech is the worse trade.
        any_recording = any(member[2] for member in members)
        tempo_by_filename[destination.name] = recording_tempo if any_recording else tts_tempo

    if merged:
        logging.info(
            f"Merged {len(merged)} timing slot(s) shared by more than one chunk: {'; '.join(merged[:8])}"
        )
    if skipped:
        logging.warning(
            f"Assembling without {len(skipped)} chunk(s) that have no usable audio: {', '.join(skipped[:8])}"
        )
    if stale:
        logging.warning(
            f"Assembling {len(stale)} chunk(s) whose audio predates the current text: {', '.join(stale[:8])}. "
            "Run the tts stage to refresh them."
        )
    if not claimed:
        raise ValueError("No chunk audio available to assemble. Generate or record segments first.")
    staged_chunks = sum(len(members) for members in claimed.values())
    logging.info(
        f"Staged {staged_chunks} chunk(s) as {len(claimed)} clip(s) for assembly in [{staging}]"
    )
    return staging, tempo_by_filename


def build_voiceover(path_mp3, project_root, transcript, all_params, current_voice_hash):
    staging, tempo_by_filename = stage_segments_for_build(
        project_root, transcript, all_params, current_voice_hash
    )
    try:
        temp_dir_combine = update_tts_audio(path_mp3, str(staging), tempo_by_filename=tempo_by_filename)
        shift_seconds = None
        raw_shift = all_params.get("voiceover_shift")
        if raw_shift not in (None, ""):
            try:
                shift_seconds = float(raw_shift)
            except (TypeError, ValueError):
                logging.info(f"Invalid voiceover_shift value, using default: {raw_shift}")
        logging.info(f"combinne_with_original_audio({path_mp3}, {temp_dir_combine})")
        combinne_with_original_audio(path_mp3, temp_dir_combine, shift_seconds=shift_seconds)
        logging.info("combinne_with_original_audio finished")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def clear_voiceover_chunks(path, project_root):
    """A scoped rerun must not silently scope the next full run."""
    targets = [Path(project_root) / "config" / "params.json", Path(naming_convention(path, "params"))]
    for target in targets:
        if not target.exists():
            continue
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or "voiceover_chunks" not in data:
            continue
        data.pop("voiceover_chunks")
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main(path, existing_dir=None, redo_segment=None, mode="all", chunk_ids=None):

    setup_logging_with_appinsights(path)

    path_mp3 = naming_convention(path, "mp3")
    path_improved = naming_convention(path, "improved")
    project_params = read_project_params(path)
    all_params = get_all_params(path=path)
    project_root = resolve_project_root(path, path_improved)
    current_voice_hash = project_voice_hash(project_params)
    seg.store_dir(project_root).mkdir(parents=True, exist_ok=True)
    logging.info(f"Voiceover mode [{mode}], segment store [{seg.store_dir(project_root)}]")

    transcript = load_transcript(path_improved)
    if seg.assign_chunk_ids(transcript):
        logging.info("Assigned stable chunk ids to the improved transcript")
        save_json_with_upload(path_improved, transcript)

    retire_orphaned_segments(project_root, transcript)

    if existing_dir:
        logging.warning("--dir is deprecated; importing its contents into the segment store instead")
        import_existing_audio_dir(
            project_root, transcript, existing_dir, all_params, current_voice_hash
        )

    # Azure deployments still deliver recordings as a storage-account download.
    if parse_legacy_bool(project_params.get("custom_recording", False)) and file_from_sta(path):
        legacy_dir = prepare_custom_recording_dir(
            path, project_params.get("user_id", "unknown"), project_params.get("filename", "unknown")
        )
        if legacy_dir:
            import_existing_audio_dir(
                project_root, transcript, legacy_dir, all_params, current_voice_hash,
                source=seg.SOURCE_RECORDING,
            )

    import_recordings_zip(project_root, transcript, all_params)
    ingest_pending_recordings(project_root, transcript, all_params)

    forced = set(chunk_ids or []) | parse_chunk_ids(all_params.get("voiceover_chunks"))
    if redo_segment:
        logging.warning("--redo-segment is deprecated; use --mode synthesize --chunks <chunk_id>")
        forced.add(chunk_id_for_segment_key(transcript, redo_segment))

    if mode in ("synthesize", "all"):
        try:
            synthesize_missing_segments(
                path, project_root, transcript, all_params, current_voice_hash,
                forced_ids=forced or None,
            )
        finally:
            # Even a failed run should leave the transcript describing what exists.
            sync_audio_blocks(project_root, transcript, all_params, path_improved, current_voice_hash)
        clear_voiceover_chunks(path, project_root)
    else:
        sync_audio_blocks(project_root, transcript, all_params, path_improved, current_voice_hash)

    if mode in ("build", "all"):
        build_voiceover(path_mp3, project_root, transcript, all_params, current_voice_hash)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    parser.add_argument(
        '--mode', type=str, choices=['all', 'synthesize', 'build'], default='all',
        help="synthesize: fill the segment store only. build: assemble from the store only.",
    )
    parser.add_argument(
        '--chunks', type=str, required=False, default=None,
        help="Comma-separated chunk ids to regenerate, e.g. 'c012,c037'. Implies forced regeneration.",
    )
    parser.add_argument(
        '-d', '--dir', type=str, required=False, default=None,
        help="Deprecated. A pre-store synthesis directory to import into the segment store.",
    )
    parser.add_argument(
        '--redo-segment', type=str, required=False, default=None,
        help="Deprecated alias for --chunks, keyed on '<start>-<end>' instead of a chunk id.",
    )
    args = parser.parse_args()
    main(
        args.path,
        args.dir,
        redo_segment=args.redo_segment,
        mode=args.mode,
        chunk_ids=parse_chunk_ids(args.chunks),
    )
