import tempfile
from shared_functions import *
from common.shared_functions_common import get_url_service, get_common_parameters, get_value_by_path
import requests
from bs4 import BeautifulSoup

def download_video_ytdlp(url, save_folder):
    logging.info(f"Downloading video: {url}")
    try:
        yt_dlp_cmd = [
            'yt-dlp',
            '--merge-output-format', 'mp4',
            '-o', os.path.join(save_folder, '%(title)s.%(ext)s'),
            url
        ]
        subprocess.run(yt_dlp_cmd, check=True)
        # Find the downloaded video file
        for file in os.listdir(save_folder):
            if file.endswith(".mp4"):
                video_file = os.path.join(save_folder, file)
                break
        logging.info(f"Video downloaded: {video_file}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error downloading video: {e}")
        raise
    return video_file

def download_apple_podcasts(url, save_folder, stream_url=None):
    # get the podcast length, name and episode number from the url
    # return a dictionary with the length, name
    # example url: https://podcasts.apple.com/ru/podcast/nyfiken-p%C3%A5/id1528951735?i=1000667930692
    if not stream_url:
        # get episode page
        url_data = requests.get(url, timeout=15)
        url_data.raise_for_status()
        soup = BeautifulSoup(url_data.text, 'html.parser')

        script_tag = soup.find('script', id='serialized-server-data', type='application/json')
        if not script_tag or not script_tag.string:
            raise ValueError(f"Apple Podcasts metadata script not found for url [{url}]")

        data_str = script_tag.string  # Extract the raw JSON string
        data = json.loads(data_str)   # Parse the JSON into a Python object (list/dict)
        # find mp3 link
        apple_fields = get_common_parameters("apple_podcasts_fields")
        stream_url_path = apple_fields["streamUrl"]
        stream_url = get_value_by_path(data, stream_url_path)
    logging.info(f"{stream_url = }")

    if not stream_url:
        logging.error(f"No stream url found for {url}")
        logging.error(f"{data = }")
        return None

    # download the file
    response = requests.get(stream_url, timeout=60)
    response.raise_for_status()
    with open(os.path.join(save_folder, 'file.mp3'), 'wb') as f:
        f.write(response.content)
    return os.path.join(save_folder, 'file.mp3')

def main(path):

    setup_logging_with_appinsights(path)

    logging.info(f"Processing url for {path = }")

    import json
    url_data = json.loads( get_palintext_content(path) )
    url = url_data["url"]
    logging.info(f"{url = }")

    type_of_url = get_url_service(url)
    logging.info(f"{type_of_url = }")

    if type_of_url == "youtube":
        # download the file from video url
        local_file_path = get_local_file_path(path)
        mp4_file_local = naming_convention(local_file_path, "mp4")
        with tempfile.TemporaryDirectory() as temp_dir:
            logging.info(f"Temp dir: {temp_dir}")
            video_file = None
            try:
                video_file = download_video_ytdlp(url, temp_dir)
                logging.info(f"Video downloaded: {video_file}")
                copy_or_upload(source_path=video_file, destination_path=mp4_file_local)
                logging.info(f"Video saved to: {mp4_file_local}")
            except Exception as e:
                logging.error(f"Error downloading video: {e}")

            if not video_file:
                logging.error(f"Failed to download video: {url}")
                return

        # upload the file to the blob
        mp4_file_path = naming_convention(path, "mp4")
        _ = azure_blob_transfer(
            blobfilepath=mp4_file_path,
            localfilepath=mp4_file_local,
            operation="upload"
        )
        _ = extract_audio_and_upload(mp4_file_path, mp4_file_local)


    elif type_of_url == "apple_podcasts":
        logging.info(f"Apple podcasts url: {url}")
        local_file_path = get_local_file_path(path)
        mp3_file_local = naming_convention(local_file_path, "mp3")
        with tempfile.TemporaryDirectory() as temp_dir:
            logging.info(f"Temp dir: {temp_dir}")
            file = None
            try:
                file = download_apple_podcasts(url, temp_dir, stream_url=url_data.get("streamUrl"))
                logging.info(f"Mp3 downloaded: {file}")
                if file:
                    copy_or_upload(source_path=file, destination_path=mp3_file_local)
                    logging.info(f"Mp3 saved to: {mp3_file_local}")
            except Exception as e:
                logging.error(f"Error downloading audio: {e}")

            if not file:
                logging.error(f"Failed to download audio: {url}")
                return


        # upload the file to the blob
        mp3_file_path = naming_convention(path, "mp3")
        _ = azure_blob_transfer(
            blobfilepath=mp3_file_path,
            localfilepath=mp3_file_local,
            operation="upload"
        )

        pass

    else:
        logging.error(f"Unsupported url service: {type_of_url}")
        return


    pass




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    main(args.path)



    pass
