import json
import logging
import os

import requests

def get_common_parameters(parameter, parameters_path='common_parameters.json'):
    
    default_params = load_settings_from_file(parameters_path)

    # Return the requested parameter
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


def get_ui_stages( level: str = "basic") -> list:
    allowed_stages_levels = ["basic", "advanced"]
    if level not in allowed_stages_levels:
        raise ValueError(f"Unsupported level [{level}]. Allowed values are: {allowed_stages_levels = }")
    res = get_common_parameters(f"supported_processing_stages_{level}")
    return res

def get_stages_to_run_from_ui(stages_ui: str) -> str:
    print(f"stages_ui = {stages_ui}")
    if stages_ui == "all":
        return "all"
    if stages_ui == "":
        return "basic"
    if stages_ui.find("basic") > -1:
        level = "basic"
    elif stages_ui.find("advanced") > -1:
        level = "advanced"
    else:
        raise ValueError(f"Unsupported stages [{stages_ui}]")
    

    supported_stages_list = get_ui_stages(level)

    stages_ui_list = stages_ui.split("+")
    stages_to_run = ""
    for stage_ui in stages_ui_list:
        for supported_stage in supported_stages_list:
            if stage_ui == supported_stage["name"]:
                stages_to_run += f"{supported_stage['backend_mapping']}+"

    # remove the last '+'
    stages_to_run = stages_to_run[:-1]



    return stages_to_run

def load_settings_from_file(path):
    # Get the directory where the current script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Construct the full path to the parameters file
    full_path = os.path.join(script_dir, path)

    if not os.path.exists(full_path):
        raise FileNotFoundError("Settings file not found")

    # Load default parameters
    with open(full_path, 'r') as file:
        data = json.load(file)

    return data
    

def get_user_visibility_parameters(parameters_path='user_visibility_parameters.json'):

    user_visibility_parameters = load_settings_from_file(parameters_path)

    return user_visibility_parameters

def get_valid_levels(parameters_path='user_visibility_parameters.json'):
    settings = load_settings_from_file(parameters_path)
    # Extract levels from the first setting (assuming all settings have the same levels)
    first_setting_key = next(iter(settings))
    return list(settings[first_setting_key].keys())

def get_initial_level():
    levels = get_valid_levels()
    return levels[1]

def get_all_supported_levels():
    levels = get_valid_levels()
    # remove the first level
    levels.pop(0)
    return levels


# check if the file is supported type
def is_supported_file_type(filename):
    print(f"is_supported_file_type: {filename = }")
    supported_file_types = get_common_parameters("supported_file_types")
    in_supported_file_types = filename.split('.')[-1] in supported_file_types
    url_processing = filename.endswith(".url")
    return in_supported_file_types or url_processing


def check_url_is_video(url):
    # Check for various YouTube URL patterns
    if "youtube.com" in url or "youtu.be" in url:
        return True
    # Check if url is an apple podcasts url
    if "apple.com" in url:
        return False
    # Check if url is a spotify url
    if "spotify.com" in url:
        return False
    return None


def get_url_service(url):
    # Check for various YouTube URL patterns
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    # Check if url is an apple podcasts url
    if "apple.com" in url:
        return "apple_podcasts"
    # Check if url is a spotify url
    if "spotify.com" in url:
        return "spotify"
    return None

def get_value_by_path(data, path):
    """
    Helper to extract a value from a nested dict/list using a path list.
    """
    for key in path:
        if isinstance(data, list) and isinstance(key, int):
            if key < len(data):
                data = data[key]
            else:
                return None
        elif isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    return data