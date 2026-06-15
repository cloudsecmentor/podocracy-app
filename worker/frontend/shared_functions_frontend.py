from io import BytesIO
import logging
import os
import requests

def get_api_data(uri: str, debug: bool = False):
    from frontend.project.config import API_KEY
    headers = {
        'X-Api-Key': API_KEY
        }

    print(f"get_api_data {uri = }")
    # print(f"get_api_data {headers = }")
    response = requests.get(uri, headers=headers)
    if debug:
        logging.info(f"get_api_data, with X-Api-Key: Call URI: {uri}")


    return response.json()

def get_data_from_api(url: str, body: dict = {}, debug: bool = False):
    # from frontend.project.config import API_KEY
    API_KEY = get_api_key()

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "accept": "application/json"
    }
    payload = body

    try:
        response = requests.get(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        logging.debug(f"get_data_from_api {url = } response: {data = }")
        return data
    except requests.exceptions.RequestException as e:
        logging.info(f"Error get_data_from_api: {e}")
        # Handle error appropriately
        return None

def post_data_to_api(url: str, body: dict = {}, debug: bool = False):
    # from frontend.project.config import API_KEY
    API_KEY = get_api_key()
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    payload = body

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        logging.debug(f"post_data_to_api {url = } response: {data = }")
        return data
    except requests.exceptions.RequestException as e:
        logging.info(f"Error post_data_to_api: {e}")
        # Handle error appropriately
        return None

def get_api_key():
    API_KEY = os.getenv("API_KEY")
    if API_KEY:
        return API_KEY
    # # TODO add try/error
    # from frontend.project.config import API_KEY
    # if API_KEY:
    #     return API_KEY
    # from backend.processing_container.shared_functions import API_KEY
    # if API_KEY:
    #     return API_KEY
    # from api.shared_functions_api import API_KEY
    # if API_KEY:
    #     return API_KEY
    raise ValueError("API_KEY is not set")


def put_data_to_api(url: str, body: dict = {}, debug: bool = False):
    # from frontend.project.config import API_KEY
    API_KEY = get_api_key()
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    payload = body

    try:
        print(f"put_data_to_api {url = }, headers: {headers = }, json: {payload = }")
        response = requests.put(url, headers=headers, json=payload)
        print(f"put_data_to_api {url = } response: {response = }")
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        logging.debug(f"put_data_to_api {url = } response: {data = }")
        return data
    except requests.exceptions.RequestException as e:
        logging.info(f"Error put_data_to_api: {e}")
        # Handle error appropriately
        return None



def delete_data_to_api(url: str, body: dict = {}, debug: bool = False):
    # from frontend.project.config import API_KEY
    API_KEY = get_api_key()
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "accept": "application/json"
    }
    payload = body

    try:
        response = requests.delete(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        if debug:
            logging.debug(f"post_data_to_api {url = } response: {data = }")
        return data
    except requests.exceptions.RequestException as e:
        logging.info(f"Error post_data_to_api: {e}")
        # Handle error appropriately
        return None



def get_api_headers_nonjson():
    # from frontend.project.config import API_KEY
    API_KEY = get_api_key()
    return {
        "Authorization": f"Bearer {API_KEY}"
    }



def get_file_from_path(file_path):
    from werkzeug.datastructures import FileStorage
    """
    Create a FileStorage object from a file on disk.

    :param file_path: Path to the file on disk.
    :return: FileStorage object.
    """
    # Create a FileStorage object
    with open(file_path, 'rb') as f:
        audio_stream = BytesIO(f.read())
        file = FileStorage(stream=audio_stream, filename=file_path, content_type='audio/mpeg')

    # Verify content type
    if file.content_type != 'audio/mpeg':
        logging.warning(f"Warning: Content type is {file.content_type}, expected 'audio/mpeg'")

    return file


def get_container_name_from_id(id):
    # need to replace with api call also for api
    return f"{id}"



def custom_get_blob_client(container_name, blob_name):
    from azure.storage.blob import BlobServiceClient
    connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
    blob_service_client = BlobServiceClient.from_connection_string(connect_str)
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    return blob_client




def get_supported_languages():
    res = [
        '--choose--',
        'Bulgarian (BG)',
        'Czech (CS)',
        'Danish (DA)',
        'German (DE)',
        'Greek (EL)',
        'English (British) (EN-GB)',
        'English (American) (EN-US)',
        'Spanish (ES)',
        'Estonian (ET)',
        'Finnish (FI)',
        'French (FR)',
        'Hungarian (HU)',
        'Indonesian (ID)',
        'Italian (IT)',
        'Japanese (JA)',
        'Korean (KO)',
        'Lithuanian (LT)',
        'Latvian (LV)',
        'Norwegian (NB)',
        'Dutch (NL)',
        'Polish (PL)',
        'Portuguese (Brazilian) (PT-BR)',
        'Portuguese (European) (PT-PT)',
        'Romanian (RO)',
        'Russian (RU)',
        'Slovak (SK)',
        'Slovenian (SL)',
        'Swedish (SV)',
        'Turkish (TR)',
        'Ukrainian (UK)',
        'Chinese (simplified) (ZH)',
        'Chinese (simplified) (ZH-HANS)'
    ]
    return res


def get_supported_models():
    res = [
        "base.en",
        "small",
        "medium",
        "large",
    ]
    return res


def get_display_name(user_id, base_name):
    from frontend.project.config import API_URI
    uri = f"{API_URI}/file/display_name/get?user_id={user_id}&base_name={base_name}"
    data = get_data_from_api(uri)
    # handle HTTPError
    if data is None:
        return base_name
    return data['display_name'] if "display_name" in data else base_name

def get_supported_voices():
    from frontend.project.config import API_URI
    voices = get_data_from_api(f"{API_URI}/settings/voices")
    # logging.info(f"get_supported_voices: {voices =}")
    return voices["voices"]


def send_email_to_master(subject, body):
    from frontend.project.config import API_URI
    uri = f"{API_URI}/info/send_email"
    data = post_data_to_api(uri, {"subject": subject, "body": body})
    return data
