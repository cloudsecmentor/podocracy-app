import subprocess
import os

def is_ffmpeg_installed():
    """Check if FFmpeg is already installed."""
    try:
        result = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            print("FFmpeg is already installed.")
            return True
        else:
            return False
    except FileNotFoundError:
        return False

def install_ffmpeg_and_bc():
    """Install FFmpeg and bc packages."""
    try:
        print("Installing FFmpeg and bc...")
        subprocess.run(
            "apt-get update && apt-get install -y ffmpeg bc build-essential && apt-get clean && rm -rf /var/lib/apt/lists/*", 
            shell=True, 
            check=True
        )
        print("FFmpeg and bc installation completed.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install FFmpeg and bc: {e}")
        exit(1)

def install_python_packages():
    """Install Python packages from the requirements file."""
    try:
        print("Installing Python packages...")
        subprocess.run(
            "pip install --no-cache-dir -r req.backend.txt", 
            shell=True, 
            check=True
        )
        print("Python package installation completed.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install Python packages: {e}")
        exit(1)

def show_python_packages():
    """Show Python packages."""
    print("Python packages:")
    subprocess.run(["pip", "freeze"], check=True)

def check_and_install_dependencies():
    if not is_ffmpeg_installed():
        print("Not found FFmpeg, installing...")
        install_ffmpeg_and_bc()
        install_python_packages()
        show_python_packages()
    else:
        print("Skipping installation as FFmpeg is already present.")

check_and_install_dependencies()

def retrive_secrets():
    """Retrieve secrets keyvault."""
    env = os.getenv("ENV")
    current_directory = os.path.abspath(os.path.dirname(__file__))
    print(f"Current directory: {current_directory}")
    if not env:
        print("ENV is not set, skipping secrets retrieval")
        return
    # test if there is a .env file in the current directory
    if os.path.exists(f"{current_directory}/.env"):
        print(".env file found in the current directory, skipping secrets retrieval")
        return
    try:
        print(f"Retrieving secrets from keyvault for {env} environment...")
        # on VM we need to login with az login --identity
        print("Logging in to Azure CLI...")
        subprocess.run(["az", "login", "--identity"], check=True)
        subprocess.run(["python", 
                        f"{current_directory}/../../infra/retrive_secrets/retrieve_secrets.py", 
                        "--env", env, 
                        "--destination", "local", 
                        "--api_location", "azure"]
                        )
    except Exception as e:
        print(f"Failed to retrieve secrets from keyvault: {e}")
        exit(1)

retrive_secrets()

import sys

# Add the parent directory of `common` to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Print each directory in the Python path
for path in sys.path:
    print(path)

from common.shared_functions_common import is_supported_file_type
from shared_functions import *


def collect_logs(path):
    ## list all files in current folder with *.log extension
    # zip into a single file with the file_path.logs.zip
    # upload using azure_blob_transfer(blobfilepath=file_path, operation="upload")
    file_path = get_local_file_path(path)
    localfilepath= naming_convention(file_path, "logs.zip" ) 
    logs_file_path = naming_convention(path, "logs.zip" )
    logging.info(f"Collecting logs from ./*log to {localfilepath}")
    import zipfile
    import glob
    with zipfile.ZipFile(f"{localfilepath}", "w") as z:
        for filename in glob.glob(f"{get_local_processing_directory()}/.log/*.log"):
            z.write(filename)
    copy_or_upload(source_path = localfilepath, destination_path =  logs_file_path)

    # _ = azure_blob_transfer(
    #     blobfilepath=logs_file_path, 
    #     localfilepath=f"{file_path}.logs.zip", 
    #     operation="upload"
    #     )
    logging.info(f"Logs uploaded to: {file_path}.logs.zip")

    return None
            

def zipdir(path, ziph):
      # ziph is zipfile handle
      for root, dirs, files in os.walk(path):
          for file in files:
              file_path = os.path.join(root, file)
              # Compute the archive name: remove the base directory path
              arcname = os.path.relpath(file_path, os.path.dirname(path))
              ziph.write(file_path, arcname=arcname)


def collect_logs_and_data(path):
    ## list all files in current folder with *.log extension
    # zip into a single file with the file_path.logs.zip
    # upload using azure_blob_transfer(blobfilepath=file_path, operation="upload")
    file_path = get_local_file_path(path)
    timestamp = get_timestamp()
    localfilepath= naming_convention(file_path, "logs.zip" ).replace(".zip", f"_{timestamp}.zip")
    logs_file_path = naming_convention(path, "logs.zip" ).replace(".zip", f"_{timestamp}.zip")
    logging.info(f"Collecting logs from ./*log and data from {localfilepath}")
    import zipfile
    import glob
    with zipfile.ZipFile(f"{localfilepath}", "w") as z:
        logging.info(f"Creating zip file {localfilepath}")
        zipdir(f"{get_local_processing_directory()}/.log", z)

        # list all directories in current folder and zip them
        directories = [obj for obj in glob.glob(f"{get_local_processing_directory()}/*", recursive=True) if os.path.isdir(obj)]
        for directory in directories:
            logging.info(f"adding to zip {directory=}")
            # zip the directory
            zipdir(directory, z)
    copy_or_upload(source_path = localfilepath, destination_path =  logs_file_path)

    logging.info(f"Logs and data are uploaded to: {logs_file_path}")

    return None
       

def add_pre_post_processing(stages, scripts, path, current_directory, custom_subtitles=False):
    logging.info(f"Updating stages and scripts: {stages=} {scripts=}")
    # if processing url file, add url processing script
    if is_supported_file_type(path):
        logging.info(f"Adding supported file type pre- and post-processing scripts for file: {path}")
        preprocess_script = [
            ("preprocess",    f"{current_directory}/pd-005-preprocess.py", ["-p", path]),
        ]
        scripts = preprocess_script + scripts
        stages = "preprocess+" + stages if stages != "all" else stages
    if url_processing(path):
        logging.info(f"Adding url preprocessing script for file: {path}")
        url_preprocess_script = [
            ("url",    f"{current_directory}/pd-005-url-processing.py", ["-p", path]),
        ]
        scripts = url_preprocess_script + scripts
        stages = "url+" + stages if stages != "all" else stages
    if custom_subtitles:
        logging.info("Custom subtitles enabled, adding subtitles preprocessing stage.")
        scripts = [("subtitles", f"{current_directory}/pd-007-subtitles.py", ["-p", path])] + scripts
        stages = "subtitles+" + stages if stages != "all" else stages
    # for both supported file types and url files
    postprocess_script = [
        ("postprocess",    f"{current_directory}/pd-055-postprocess.py", ["-p", path]),
    ]
    scripts = scripts + postprocess_script
    stages = stages + "+postprocess" if stages != "all" else stages

    return stages, scripts

def main(path, time2sleep=0):


    setup_logging_with_appinsights(path)

    # ## slip a few  minutes
    logging.info(f"Slipping {time2sleep} minutes before starting processing")
    import time
    time.sleep(time2sleep*60)

    ## read params
    try:
        params = read_project_params(path)
    except Exception as e:
        logging.error(f"Failed to read params: {e}")
        return None
    logging.info(f"Params: {params}")

    # info for updating the status via API
    base_name = naming_convention(path, "base_name")
    user_id = params["user_id"]


    # check if file from params is the same as the file passed
    if params["filename"] != path.split("/")[-1]:
        logging.error(f"File in params {params['filename']} is not the same as the file passed {path}")
        return None


    # Get the current directory of the script
    current_directory = os.path.abspath(os.path.dirname(__file__))

    logging.info(f"Current Directory: {current_directory}")

    if not get_params("whisper_api", path=path): 
        try:
            if not params.get("whisper_model"):
                whisper_model = get_params("whisper_default_model_local")
            else:
                whisper_model = params["whisper_model"]
        except Exception as e:
            logging.error(f"Failed to get whisper model: {e}")
    else:
        whisper_model = "NA - using API"
    logging.info(f"whisper model: {whisper_model}")


    # List of script names and their arguments
    scripts = [
        ("transcribe", f"{current_directory}/pd-010-raw-transcribe.py", ["-p", path, "-s", whisper_model]),
        ("combine",    f"{current_directory}/pd-020-combine.py", ["-p", path]),
        ("timesync",   f"{current_directory}/pd-025-timesync.py", ["-p", path]),
        ("translate",  f"{current_directory}/pd-030-translate.py", ["-p", path]),
        ("customize",  f"{current_directory}/pd-035-customize.py", ["-p", path]),
        ("improve",    f"{current_directory}/pd-040-improve.py", ["-p", path]),
        ("voiceover",  f"{current_directory}/pd-050-voiceover.py", ["-p", path]),
    ]

    stages = params["stages_to_run"]
    custom_subtitles = params.get("custom_subtitles", "false") == "true"

    stages, scripts = add_pre_post_processing(
        stages,
        scripts,
        path,
        current_directory,
        custom_subtitles=custom_subtitles
    )


    stages_list = stages.split("+")
    stages_set = set(stages_list)
    if stages != "all":
        if stages_set & {"translate", "improve", "voiceover"}:
            stages_set.update({"combine", "timesync"})
        if "improve" in stages_set:
            stages_set.add("customize")
        if "voiceover" in stages_set:
            stages_set.update({"improve", "translate"})
    logging.info(f"Stages to execute: {stages_list}")

    # Running each script with its arguments
    total_stages = len(scripts)
    stage_failures: list[str] = []

    for current_stage_count, (stage, script, args) in enumerate(scripts):
        if stage not in stages_set and stages != "all":
            logging.info(f"Skipping stage [{stage}]: {script} with args: {args}")
            continue
        
        # Calculate progress as the ratio of current stage count to total stages
        progress = int((current_stage_count / total_stages) * 100)
        
        # Local worker uses status.json; skip remote API status updates when API_URI is unset.
        # update_status(user_id=user_id, project_id=base_name, state=stage, progress=progress)

        logging.info(f"Running stage [{stage}]: {script} with args: {args}, timestamp: [{get_timestamp()}]")
        try:
            log_dir = f"{get_local_processing_directory()}/.log"
            os.makedirs(log_dir, exist_ok=True)
            stage_log_path = f"{log_dir}/{stage}_{get_timestamp()}.log"

            result = subprocess.run(
                ["python", script] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            with open(stage_log_path, "w") as f:
                f.write(result.stdout or "")

            if result.returncode != 0:
                stage_failures.append(stage)
                tail = (result.stdout or "")[-2000:]
                logging.error(
                    f"Stage [{stage}] failed with return code {result.returncode}. "
                    f"Log: {stage_log_path}\n{tail}"
                )
            else:
                logging.info(f"Stage [{stage}] completed successfully. Log: {stage_log_path}")

        except Exception as e:
            stage_failures.append(stage)
            logging.error(f"Failed to run stage [{stage}]: {script} with args: {args}, error: {e}")

    if stage_failures:
        logging.error(f"Pipeline failed stages: {stage_failures}")
        collect_logs_and_data(path)
        sys.exit(1)

    # update_status(user_id=user_id, project_id=base_name, state="completed", progress=100)

    # ## slip a few  minutes
    logging.info(f"Slipping {time2sleep} minutes post processing")
    import time
    time.sleep(time2sleep*60)

    # collect logs and upload to blob
    logging.info("Collecting logs")
    collect_logs_and_data(path)

    return None


if __name__ == "__main__":

    ## example python pd-00-orchestrator.py -p /blobServices/default/containers/000002/blobs/e134-01min.mp3 


    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="""path to file in .mp3 or .mp4 or .url format, 
                        all other files will be generated according to naming convention.
                        Example blob: /blobServices/default/containers/000001/blobs/e006-15min_mp4.mp3
                        Example local: /mnt/data/e006-15min_mp4.mp3""")
    parser.add_argument("-s", "--time2sleep", help="time to sleep in minutes before starting processing", default="0")
    args = parser.parse_args()

    try:
        main(path = args.path, time2sleep = int(args.time2sleep))
    except Exception as e:
        logging.error(f"Error in main: {e}")




    print_final_line()
