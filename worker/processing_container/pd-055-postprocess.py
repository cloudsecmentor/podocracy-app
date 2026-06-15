from shared_functions import *
from common.shared_functions_common import check_url_is_video


def main(path):

    setup_logging_with_appinsights(path)

    logging.info(f"Postprocessing file for {path = }")


    if check_url_is_video(path) or video_processing(path):

        # 1. merge video and audio into a single file .voiceover.mp4
        logging.info(f"Postprocessing file: {path}")
        path_video_in = naming_convention(path, "mp4") if url_processing(path) else path
        local_path_video_in = get_local_path_with_download(path_video_in)
        local_path_audio_in = get_local_path_with_download(naming_convention(path, "mp3_voiceover"))
        local_path_video_out = naming_convention(local_path_video_in, "mp4_voiceover")
        try:
            res = merge_video_audio(local_path_video_in, local_path_audio_in, local_path_video_out)
        except:
            logging.error(f"Failed to merge video and audio for {local_path_video_in = } and {local_path_audio_in = }")
            return
        if res:
            logging.info(f"{local_path_video_in = } file merged with {local_path_audio_in = } and saved to {local_path_video_out = }")
        
        path_mp4_voiceover = naming_convention(path, "mp4_voiceover")
        copy_or_upload(local_path_video_out, path_mp4_voiceover)

        logging.info(f"Copied final video to [{path_mp4_voiceover}]")

        # 2. check if the file is too big and create a preview version or copy itself to preview version
        # get the size of the file in MB
        size = os.path.getsize(local_path_video_out) / (1024 * 1024)
        logging.info(f"Size of the file: {size:.2f} MB")
        max_video_file_size_mb = get_params("max_video_file_size_mb")
        if size > max_video_file_size_mb:
            logging.warning(f"File size is greater than {max_video_file_size_mb} MB. Creating a preview version.")
            local_path_video_out_preview = naming_convention(local_path_video_out, "mp4_voiceover_preview")
            limit_preview_length_minutes = get_common_parameters("limit_preview_length_minutes")
            res = create_preview_version(local_path_video_out, local_path_video_out_preview, max_video_file_size_mb, limit_preview_length_minutes)
            if res:
                logging.info(f"Preview version created and saved to [{local_path_video_out_preview}]")
                path_mp4_voiceover_preview = naming_convention(path, "mp4_voiceover_preview")
                copy_or_upload(local_path_video_out_preview, path_mp4_voiceover_preview)
        else:
            logging.error(f"File size is less than {max_video_file_size_mb} MB. Copy file to preview version.")
            path_mp4_voiceover_preview = naming_convention(path, "mp4_voiceover_preview")
            copy_or_upload(local_path_video_out, path_mp4_voiceover_preview)
    else:
        logging.info(f"File does not require postprocessing: {path}")


    pass




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    main(args.path)



    pass