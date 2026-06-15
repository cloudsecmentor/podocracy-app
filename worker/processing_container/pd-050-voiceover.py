import glob
import re
from tenacity import retry, wait_exponential, stop_after_attempt, before_sleep_log
from shared_functions import *
from azure.storage.blob import BlobServiceClient
from frontend.shared_functions_frontend import get_container_name_from_id

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
    if get_params("tts_api") == "elevenlabs":
        logging.info(f"Using ElevenLabs TTS API for {speech_file_path}")
        return tts_elevenlabs(text, speech_file_path)
    
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




def update_tts_audio(path, path_synthesis, custom_speedup=None):
    import os
    # download original file
    local_file_orig = get_local_path_with_download(path)

    temp_dir_combine = create_timestamped_directory(local_file_orig)

    # get list of all files in a directory
    src_format = "ogg" ## change to params later
    import glob
    src_trans_files = sorted(glob.glob(f"{path_synthesis}/*.{src_format}" ))


    speedup = custom_speedup or get_params("speedup_value")
    if speedup != 1.0 :
        logging.info (f"Changing the speed on {str ( (speedup - 1) * 100 )}%. Consider 'Change Tempo' effect in Audacity for better quality..")

    for infile in src_trans_files:
        if (os.path.getsize(infile) ==0 ): continue
        infile_basename = os.path.basename(infile)
        outfile = f"{temp_dir_combine}/{infile_basename}"  ## 
        logging.info (f"Updating {infile} and saving to {outfile}")


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


def main(path, existing_dir=None):

    setup_logging_with_appinsights(path)

    path_mp3 = naming_convention(path, "mp3")
    project_params = read_project_params(path)
    custom_recording = parse_legacy_bool(project_params.get("custom_recording", False))
    custom_speedup = None
    voiceover_tempo = project_params.get("voiceover_tempo")
    if voiceover_tempo is not None:
        try:
            custom_speedup = float(voiceover_tempo)
        except (TypeError, ValueError):
            logging.info(f"Invalid voiceover_tempo value, using default: {voiceover_tempo}")

    if custom_recording:
        custom_speedup = 1
        user_id = project_params.get("user_id", "unknown")
        file_name = project_params.get("filename", "unknown")
        logging.info(f"custom_recording for [{user_id=}] [{file_name=}] [{custom_speedup=}]")
        existing_dir = prepare_custom_recording_dir(path, user_id, file_name)

    if existing_dir:
        temp_dir = existing_dir
        logging.info(f"Generated audio found in directory [{temp_dir}]")
    else:

        path_improved = naming_convention(path, "improved")
        import json
        transcript_improved = json.loads( get_palintext_content(path_improved) )

        local_file = get_local_file_path(path_improved)
        temp_dir = create_timestamped_directory(local_file)
        logging.info(f"Generating audio based on file : [{path_improved}] in directory [{temp_dir}]")
        tts(transcript_improved, temp_dir, path) # TODO: add option to use ElevenLabs TTS API based on user choice - just pass path

    temp_dir_combine = update_tts_audio(path_mp3, temp_dir, custom_speedup=custom_speedup)

    logging.info(f"combinne_with_original_audio({path_mp3}, {temp_dir_combine})")
    shift_seconds = None
    if "voiceover_shift" in project_params:
        try:
            shift_seconds = float(project_params.get("voiceover_shift"))
        except (TypeError, ValueError):
            logging.info(f"Invalid voiceover_shift value, using default: {project_params.get('voiceover_shift')}")

    combinne_with_original_audio(path_mp3, temp_dir_combine, shift_seconds=shift_seconds)


    logging.info(f"combinne_with_original_audio finished")


    pass




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    parser.add_argument('-d', '--dir', type=str, required=False, help="directory to saved synthesized files", default=None)
    args = parser.parse_args()
    main(args.path, args.dir)



    pass
