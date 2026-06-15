from shared_functions import *
from frontend.shared_functions_frontend import get_data_from_api
from mutagen.mp3 import MP3
from pydub import AudioSegment


def get_mp3_length_in_minutes(file_path):
    audio = MP3(file_path)
    length_in_seconds = audio.info.length
    length_in_minutes = length_in_seconds / 60
    return length_in_minutes

def get_custom_recording(path):
    try:
        custom_recording = parse_legacy_bool(get_params("custom_recording", path = path))
    except Exception as e:
        logging.error(f"get_custom_recording: {e}")
        custom_recording = False
    return custom_recording

def get_length_to_deduct(length, stages_to_run, path):
    if not stages_to_run:
        return 0.0
    else:
        stages_waits = get_common_parameters("stages_waits")
        custom_recording = get_custom_recording(path)

        total_wait = sum(
            stages_waits[stage] / 100
            for stage in stages_to_run
            if not custom_recording or stage != "voiceover"
        )

        ## TODO: implement deduction according to level (extra stages which included in the level)
        user_id = get_user_id_from_sta_path(path)
        base_name = naming_convention(path, "base_name")
        for stage in stages_to_run:
            stage_name = stage if (not custom_recording or stage != "voiceover") else f"{stage}.custom"
            url = f"{API_URI}/stages/get_count?user_id={user_id}&base_name={base_name}&stage={stage_name}"
            count = get_data_from_api(url).get("count", 0)
            logging.info(f"get_length_to_deduct: Recorded stage run: user_id={user_id}&base_name={base_name}&stage={stage_name}, {count=}")

        final_length = length * total_wait

        logging.info(f"get_length_to_deduct: {stages_waits = }, {custom_recording = }, {total_wait = }, {final_length = }")
        return final_length

def record_stage_run(path, stages_to_run):
    logging.info(f"record_stage_run: {path=}, {stages_to_run=}")

    # get user id
    user_id = get_user_id_from_sta_path(path)
    base_name = naming_convention(path, "base_name")
    custom_recording = get_custom_recording(path)
    # record stage run
    for stage in stages_to_run:
        stage_name = stage if (not custom_recording or stage != "voiceover") else f"{stage}.custom"
        res = update_status(user_id=user_id, project_id=base_name, state=stage_name, progress=0)
        logging.info(f"record_stage_run: {res=}")
    return {"status": "success", "message": f"Stages run recorded: {stages_to_run=}, {user_id=}"}

def update_user_credits(path, mp3_local_path):
    logging.info(f"update_user_credits: {path=}, {mp3_local_path=}")
    if file_from_sta(path):
        logging.info(f"File is from storage: {path=}, updating credits")
        user_id = get_user_id_from_sta_path(path)
        try:
            stages_to_run = get_params("stages_to_run", path = path).split("+")
            logging.info(f"update_user_credits: {stages_to_run=}")
        except Exception as e:
            logging.error(f"Error update_user_credits: {e}")
        # get length of the mp3_local_path in minutes
        length = get_mp3_length_in_minutes(mp3_local_path)

        try:
            length_to_deduct = get_length_to_deduct(length, stages_to_run, path)
        except Exception as e:
            logging.error(f"get_length_to_deduct: {e}")
            length_to_deduct = 0.0

        # deduct credit
        credit_url = f"{API_URI.rstrip('/')}/v1/internal/billing/grants"
        credit_body = {
            "user_id": user_id,
            "minutes_delta": -length_to_deduct,
        }
        logging.info(f"update_user_credits: Deducting {length_to_deduct} credits from user {user_id}, {credit_url=}, {credit_body=}")
        try:
            res = post_data_to_api_backend(credit_url, credit_body, debug=True)
            logging.info(f"update_user_credits: Deducted {length_to_deduct} credits from user {user_id}, {res=}")
            # updating stages
            res = record_stage_run(path, stages_to_run)
            logging.info(f"update_user_credits: Recorded stage run: {res=}")
            sys.exit()
        except Exception as e:
            logging.error(f"update_user_credits: Error deducting credits from user {user_id}: {e}")
    else:
        logging.info(f"update_user_credits: File is not from storage: {path=}, no credits updated")
        pass

def limit_audio_length(mp3_local_path, max_free_length):
    # extract only the first max_free_length minutes of the mp3_local_path
    # and save to the same path
  
    if get_mp3_length_in_minutes(mp3_local_path) > max_free_length:
        # extract only the first max_free_length minutes of the mp3_local_path
        # and save to the same path
        audio = AudioSegment.from_mp3(mp3_local_path)
        audio = audio[:max_free_length * 60 * 1000]
        audio.export(mp3_local_path, format="mp3")
        logging.info(f"limit_audio_length: limited file to {max_free_length} minutes, {mp3_local_path=}")
        return True
    else:
        logging.info(f"limit_audio_length: file is already within the limit, {mp3_local_path=}")
        return False

def free_user_limit(path, mp3_local_path):
    # check if this is a free user
    # if so, check limit audio to a set number of minutes
    if file_from_sta(path):
        logging.info(f"free_user_limit: File is from storage: {path=}, enforcing free user limit")
        user_id = get_user_id_from_sta_path(path)
        max_free_length = get_common_parameters("max_free_length")

        # get user level
        user_url = f"{API_URI}/v1/internal/users/info"
        user_info = get_data_from_api_backend(user_url, params={"user_id": user_id})
        user_access_level = user_info["access_level"].lower()
        logging.info(f"free_user_limit: {max_free_length=}, {user_access_level=}, {user_id=}")

        if user_access_level != "free":
            return {"status": "success", "message": "User is not a free user"}

        # extract only the first max_free_length minutes of the mp3_local_path
        if limit_audio_length(mp3_local_path, max_free_length):
            mp3_remote_path = naming_convention(path, "mp3")
            copy_or_upload(mp3_local_path, mp3_remote_path)
        
            return {"status": "success", "message": "Limited file to free user limit"}
        else:
            return {"status": "success", "message": "File is already within the free user limit"}

    return {"status": "success", "message": "File is not from storage"}


def main(path):

    setup_logging_with_appinsights(path)

    logging.info(f"Preprocessing file for {path = }")

    local_path = get_local_path_with_download(path)


    if not mp3_processing(path) and not url_processing(path):
        # expect url should already have mp3 file
        mp3_local_path = extract_audio_and_upload(path, local_path)

    else:
        mp3_local_path = naming_convention(local_path, "mp3")
        logging.info(f"File does not require preprocessing: {path}")

    result = free_user_limit(path, mp3_local_path)
    logging.info(f"free_user_limit result: {result}")
    
    update_user_credits(path, mp3_local_path)

    pass




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    main(args.path)



    pass
