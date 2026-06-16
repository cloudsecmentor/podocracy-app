from shared_functions import *







def improve_text_openai_2408(episode, key_name, improved_text_key = "imp", sleep_time=0.5, custom_instructions = "", caffeinate = True):
    import time
    import copy
    episode_new = copy.deepcopy(episode) 
    remove_key_name = True ## TODO: add this as a parameter
    # add progress bar
    from tqdm import tqdm

    import subprocess
    from signal import SIGKILL
    caffeinate_proc = maybe_start_caffeinate(caffeinate)

    for chunk in tqdm(episode_new):
        logging.info(f"Original translated text: [{chunk[key_name]}]")
        chunk[improved_text_key] = improve_text_openai_chunk_2407(chunk[key_name], chunk["text"], custom_instructions)
        if remove_key_name:
            # remove the key_name from the chunk
            del chunk[key_name]
        time.sleep(sleep_time)


    maybe_stop_caffeinate(caffeinate_proc)

    return episode_new


def improve_text_openai_chunk_2403(text, text_oringinal):
    import json
    from openai import OpenAI
    import os
    import dotenv
    dotenv.load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    model = get_params("openai_model")
    # model = "gpt-4"
    format_example = '''
    {
        "language_identified": "",
        "improved_text": ""
    }
    '''

    system_message = f"""
    You are a helpful assistant designed to proofread and improve text quality
    You provide output in JSON format as presented in triple ticks:
    '''
    {format_example}
    '''
    """
    user_message = f"""{text if isinstance(text, str) else json.dumps(text)}"""
    # mispelled_words = f"""[BEMA, bemadiscipleship]""" # TODO: read this from params
    # mispelled_words = None # TODO: read this from params
    # if mispelled_words:
    #     mispelled_words_addon = f"""The text may contain a few mispelled words, in trple dollars below you can find the list of often mispelled words, please correct them: $$$ {mispelled_words} $$$"""
    # else:
    #     mispelled_words_addon = ""


    response = client.chat.completions.create(
        model=model,
        response_format={ "type": "json_object" },
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": f"""
             You are a helpful assistant designed to proofread and improve the quality of translated text. Your
task is to improve the translation of a podcast transcript excerpt. The original excerpt, provided
below in <original_text> tags, contains filler words and other elements of spoken language that may
reduce the clarity of the translation.

<original_text>
{text_oringinal}
</original_text>

The current translation of the excerpt is provided below in <translated_text> tags:

<translated_text>
{user_message}
</translated_text>

First, identify the language of the translated text and write it down.

Then, carefully proofread the translated text, looking for ways to improve its readability,
naturalness, and clarity. As you review the text, keep the following in mind:
- Convert any numbers into words
- Correct any grammatical errors, awkward phrasing, or non-native language patterns
- Make the text sound as natural and concise as possible while still retaining the original meaning
- Remove filler words (like "um", "uh", "you know", etc.) and fix informal or ungrammatical spoken
language forms where it improves clarity and flow
- Preserve the core meaning and key content of the original excerpt - do not embellish or change the
meaning

When you are finished proofreading, provide the improved translation in the following JSON format:

<format_example>
{format_example}
</format_example>

Remember, if the translation is already perfect, you can simply copy and paste it into the
"improved_text" field. The goal is to enhance clarity, flow and naturalness while preserving the
original meaning as much as possible.



            """}
        ],
        **({"response_format": {"type": "json_object"}} | ({} if str(model).startswith("gpt-5") else {"temperature": 0}))
    )
    logging.info(response.choices[0].message.content)
    res = json.loads(response.choices[0].message.content)


    return res["improved_text"]


def improve_text_openai_chunk_2407(text, text_oringinal, custom_instructions):
    import json
    from openai import OpenAI
    import os
    import dotenv
    dotenv.load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    model = get_params("openai_model")

    custom_instructions_text = f"""
     - pay special attention to the following instructions:\n {custom_instructions}
    """ if custom_instructions else ""
    # model = "gpt-4"
    format_example = '''
    {
        "language_identified": "",
        "improved_text": ""
    }
    '''

    system_message = f"""
    You are a helpful assistant designed to proofread and improve text quality
    You provide output in JSON format as presented in triple ticks:
    '''
    {format_example}
    '''
    """
    user_message = f"""{text if isinstance(text, str) else json.dumps(text)}"""
    # mispelled_words = f"""[BEMA, bemadiscipleship]""" # TODO: read this from params
    # mispelled_words = None # TODO: read this from params
    # if mispelled_words:
    #     mispelled_words_addon = f"""The text may contain a few mispelled words, in trple dollars below you can find the list of often mispelled words, please correct them: $$$ {mispelled_words} $$$"""
    # else:
    #     mispelled_words_addon = ""

    user_content = f"""
             You are a helpful assistant designed to proofread and improve the quality of translated text. Your
task is to improve the translation of a podcast transcript excerpt. The original excerpt, provided
below in <original_text> tags, contains filler words and other elements of spoken language that may
reduce the clarity of the translation.

<original_text>
{text_oringinal}
</original_text>

The current translation of the excerpt is provided below in <translated_text> tags:

<translated_text>
{user_message}
</translated_text>

First, identify the language of the translated text and write it down.

Then, carefully proofread the translated text, looking for ways to improve its readability,
naturalness, and clarity. As you review the text, keep the following in mind:
- Convert any numbers into words
- Correct any grammatical errors, awkward phrasing, or non-native language patterns
- Make the text sound as natural and concise as possible while still retaining the original meaning
- Remove filler words (like "um", "uh", "you know", etc.) and fix informal or ungrammatical spoken
language forms where it improves clarity and flow
- Preserve the core meaning and key content of the original excerpt - do not embellish or change the
meaning

{custom_instructions_text}


When you are finished proofreading, provide the improved translation in the following JSON format:

<format_example>
{format_example}
</format_example>

Remember, if the translation is already perfect, you can simply copy and paste it into the
"improved_text" field. The goal is to enhance clarity, flow and naturalness while preserving the
original meaning as much as possible.

"""


    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_content}
        ],
        **({"response_format": {"type": "json_object"}} | ({} if str(model).startswith("gpt-5") else {"temperature": 0}))
    )

    
    logging.info(f"Model responce: [{response.choices[0].message.content}]")
    res = json.loads(response.choices[0].message.content)


    return res["improved_text"]



def improve_text_openai_chunk(text):
    import json
    from openai import OpenAI
    import os
    import dotenv
    dotenv.load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    model = get_params("openai_model")
    # model = "gpt-4"
    format_example = '''
    {
        "language_identified": "",
        "improved_text": ""
    }
    '''

    system_message = f"""
    You are a helpful assistant designed to proofread and improve text quality
    You provide output in JSON format as presented in triple ticks:
    '''
    {format_example}
    '''
    """
    user_message = f"""{text if isinstance(text, str) else json.dumps(text)}"""
    # mispelled_words = f"""[BEMA, bemadiscipleship]""" # TODO: read this from params
    mispelled_words = None # TODO: read this from params
    if mispelled_words:
        mispelled_words_addon = f"""The text may contain a few mispelled words, in trple dollars below you can find the list of often mispelled words, please correct them: $$$ {mispelled_words} $$$"""
    else:
        mispelled_words_addon = ""


    response = client.chat.completions.create(
        model=model,
        response_format={ "type": "json_object" },
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": f"""
            First, identify the language of the text within the triple hashes. Then, proofread the text for readability and naturalness, ensuring it sounds like it was written by a native speaker of that language. Convert any numbers into words. Correct any grammatical errors, awkward phrasing, or non-native language patterns. Try to make the text sound as natural as possible. Also try to make the text as concise as possible while still retaining the original meaning. If the text is already perfect, you can simply copy and paste it into the improved_text field. {mispelled_words_addon}
             
             Original text: 
             
             ###
             {user_message}
             ###
            """}
        ],
        **({"response_format": {"type": "json_object"}} | ({} if str(model).startswith("gpt-5") else {"temperature": 0}))
    )
    logging.info(response.choices[0].message.content)
    res = json.loads(response.choices[0].message.content)


    return res["improved_text"]


def main(path):

    setup_logging_with_appinsights(path)
    path_translated = naming_convention(path, "translated")
    import json
    transcript_translated = json.loads( get_palintext_content(path_translated) )

    # try getting custom instructions from the params, if not available, use the default
    try:
        custom_instructions = get_params("custom_instructions", path)
        # logging.info(f"Using custom instructions from the params file: [{custom_instructions[:100]}...]")
        logging.info(f"Using custom instructions from the params file: [{custom_instructions}...]")
    except:
        custom_instructions = ""
        # logging.info(f"Using default custom instructions [{custom_instructions[:100]}...]")
        logging.info(f"Using default custom instructions [{custom_instructions}...]")

    logging.info(f"Improving translation of file : [{path_translated}]")
    transcript_improved = improve_text_openai (
        episode=transcript_translated, 
        key_name=get_params("translation_text_key"), 
        improved_text_key = get_params("improved_text_key"),
        custom_instructions = custom_instructions,
        caffeinate= not file_from_sta(path))


    path_improved = naming_convention(path, "improved")
    save_json_with_upload (path_improved, transcript_improved)



    pass




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    main(args.path)



    pass