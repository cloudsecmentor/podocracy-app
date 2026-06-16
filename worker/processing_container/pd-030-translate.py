import time
from shared_functions import *


def translate_openai(episode_in, target_lang="RU"):
    import copy
    import os

    from openai import OpenAI

    episode = copy.deepcopy(episode_in)
    episode = [chunk for chunk in episode if chunk.get("text")]
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = get_params("openai_model")
    if str(model).startswith("gpt-5"):
        kwargs = {}
    else:
        kwargs = {"temperature": 0}

    for chunk in episode:
        text_content = chunk["text"]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Translate the user's text. Return only the translation."},
                    {"role": "user", "content": f"Target language: {target_lang}\n\n{text_content}"},
                ],
                **kwargs,
            )
            translated_text = response.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"translate_openai: Error translating text: {e}")
            translated_text = text_content

        translation_key = get_params("translation_text_key")
        chunk[translation_key] = translated_text

    return episode


def translate_deepl(episode_in, target_lang="RU", deepl_delay=0.5):
    import deepl
    import os
    import dotenv
    dotenv.load_dotenv()
    auth_key = os.getenv("DEEPL_AUTH_KEY") 
    translator = deepl.Translator(auth_key)

    import copy
    episode = copy.deepcopy(episode_in)

    # Use a list comprehension to filter out chunks without text
    episode = [chunk for chunk in episode if chunk.get("text")]

    from tqdm import tqdm

    # Assuming 'episode' is an iterable
    for chunk in tqdm(episode, desc="Processing"):
        # logging.info(f"{chunk = }")
        textContent = chunk["text"]
        if not textContent:
            logging.warning(f"translate_deepl: No text in the chunk {chunk = }")
            continue
        try:
            res = translator.translate_text(textContent, target_lang=target_lang)
            dltr = res.text
        except Exception as e:
            logging.error(f"translate_deepl: Error translating text: {e}")
            dltr = textContent
        time.sleep(deepl_delay)
        
        # logging.info(f"{dltr = }")
        translation_key = get_params("translation_text_key")
        chunk[translation_key] = dltr
        # logging.info(f"{chunk = }")

    return episode

def format_deepl_target_lang(target_lang):
    # if target_lang has "(", we need to take the content in the parentheses
    # if there are few parenthesis, we need to take the last one
    if "(" in target_lang:
        return target_lang.split("(")[-1].split(")")[0].strip()
    else:
        return target_lang

def main(path):

    setup_logging_with_appinsights(path)
    path_combined = naming_convention(path, "combined")
    import json
    transcript_combined = json.loads( get_palintext_content(path_combined) )

    target_lang =  format_deepl_target_lang( get_params("language", path) )
    deepl_delay = float(get_params("deepl_delay", path))
    translation_provider = str(get_params("translation_provider", path)).strip().lower()
    logging.info(f"Translating to target language: [{target_lang}] using provider [{translation_provider}]")
    if translation_provider == "openai":
        transcript_translated = translate_openai(transcript_combined, target_lang)
    else:
        transcript_translated = translate_deepl(transcript_combined, target_lang, deepl_delay)
    logging.info(f"Translated to target language: [{target_lang}]")


    path_translated = naming_convention(path, "translated")
    save_json_with_upload (path_translated, transcript_translated)



    pass




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--path', type=str, required=True, help="Path to the mp3 file")
    args = parser.parse_args()
    main(args.path)



    pass
