import argparse

from shared_functions import *

from stt import (
    TranscriptionRequest,
    create_stt_provider,
    resolve_stt_provider_name,
    validate_transcript,
)
from stt.schema import iter_transcript_words


def build_transcript_text(transcript_raw: dict, path: str) -> str:
    transcript_words = iter_transcript_words(transcript_raw)
    transcript_sentences = combine_words_to_sentences(transcript_words, path)
    return " ".join(sentence["text"] for sentence in transcript_sentences).strip()


def transcript_txt_exists(transcript_path: str) -> bool:
    if file_from_sta(transcript_path):
        return azure_blob_exists(transcript_path)

    return os.path.exists(transcript_path)


def copy_proofread_to_transcript_txt(proofread_path: str, transcript_path: str):
    transcript_text = get_palintext_content(proofread_path)

    if file_from_sta(transcript_path):
        local_path = get_local_file_path(transcript_path)
        with open(local_path, f"w", encoding=f"utf-8") as file:
            file.write(transcript_text)
        try:
            _ = azure_blob_transfer(
                blobfilepath=transcript_path,
                localfilepath=local_path,
                operation=f"upload",
                overwrite=False,
            )
        except ResourceExistsError:
            logging.info(f"Transcript text already exists, skipping proofread transcript upload: {transcript_path}")
            return transcript_path
        logging.info(f"Proofread text uploaded as transcript to: {transcript_path}")
    else:
        try:
            with open(transcript_path, f"x", encoding=f"utf-8") as file:
                file.write(transcript_text)
        except FileExistsError:
            logging.info(f"Transcript text already exists, skipping proofread transcript save: {transcript_path}")
            return transcript_path
        logging.info(f"Proofread text copied as transcript to: {transcript_path}")

    return transcript_path


def save_transcript_txt(transcript_raw: dict, path: str):
    transcript_path = naming_convention(path, f"transcript")
    if transcript_txt_exists(transcript_path):
        logging.info(f"Transcript text already exists, skipping generated transcript save: {transcript_path}")
        return transcript_path

    proofread_path = naming_convention(path, f"proofread")
    if transcript_txt_exists(proofread_path):
        logging.info(f"Proofread text exists, copying it to transcript: {proofread_path} -> {transcript_path}")
        return copy_proofread_to_transcript_txt(proofread_path, transcript_path)

    if not transcript_raw or "segments" not in transcript_raw:
        logging.info(f"save_transcript_txt: Transcript has no segments, skipping.")
        return None

    transcript_text = build_transcript_text(transcript_raw, path)

    if file_from_sta(path):
        local_path = get_local_file_path(transcript_path)
        with open(local_path, f"w", encoding=f"utf-8") as file:
            file.write(transcript_text)
        try:
            _ = azure_blob_transfer(
                blobfilepath=transcript_path,
                localfilepath=local_path,
                operation=f"upload",
                overwrite=False,
            )
        except ResourceExistsError:
            logging.info(f"Transcript text already exists, skipping generated transcript upload: {transcript_path}")
            return transcript_path
        logging.info(f"Transcript text uploaded to: {transcript_path}")
    else:
        try:
            with open(transcript_path, f"x", encoding=f"utf-8") as file:
                file.write(transcript_text)
        except FileExistsError:
            logging.info(f"Transcript text already exists, skipping generated transcript save: {transcript_path}")
            return transcript_path
        logging.info(f"Transcript text saved to: {transcript_path}")

    return transcript_path


def resolve_provider(path, model_size=None):
    """Provider plus the parameters it runs with, honouring legacy params files."""
    project_params = read_project_params(path)
    merged_params = get_all_params(path=path)
    provider_name = resolve_stt_provider_name(project_params, defaults=merged_params)
    if model_size:
        # The orchestrator resolves the model before launching this stage.
        merged_params["stt_model"] = model_size
    return create_stt_provider(provider_name), merged_params


def transcribe(path, local_path, model_size=None):
    provider, params = resolve_provider(path, model_size)
    mp3_local_path = naming_convention(local_path, "mp3")
    logging.info(f"Transcribing with provider [{provider.name}] model [{provider.resolve_model(params)}]")

    caffeinate_proc = maybe_start_caffeinate(not file_from_sta(path))
    started_at = datetime.now()
    try:
        result = provider.transcribe(
            TranscriptionRequest(
                audio_path=mp3_local_path,
                params=params,
                language=params.get("source_language") or None,
                work_dir=get_local_processing_directory(),
                logger=logging.getLogger(),
            )
        )
    finally:
        maybe_stop_caffeinate(caffeinate_proc)

    elapsed = datetime.now() - started_at
    logging.info(f"Transcription finished in {elapsed}")
    return result


def save_provider_response(result, path, local_path):
    """Keep the untouched provider payload beside the normalized transcript."""
    save_file = naming_convention(naming_convention(local_path, "mp3"), "stt_provider_response")
    save_blob = naming_convention(path, "stt_provider_response")
    write_json(result.provider_response, filename=save_file)
    logging.info(f"Provider response saved to: {save_file}")
    if file_from_sta(path):
        _ = azure_blob_transfer(blobfilepath=save_blob, operation="upload")
        logging.info(f"Provider response uploaded to: {save_blob}")
    return save_blob


def main(path, model_size=None):
    local_path = get_local_path_with_download(path)

    setup_logging_with_appinsights(local_path)

    result = transcribe(path, local_path, model_size)

    provider_response_blob = save_provider_response(result, path, local_path)
    result.transcript.provider_response_path = os.path.basename(provider_response_blob)

    transcript_raw = validate_transcript(result.transcript.to_dict())

    saveFile = naming_convention(naming_convention(local_path, "mp3"), "raw")
    saveBlob = naming_convention(path, "raw")
    write_json(transcript_raw, filename=saveFile)
    logging.info(f"Result saved to: {saveFile}")
    if file_from_sta(path):
        _ = azure_blob_transfer(blobfilepath=saveBlob, operation="upload")
        logging.info(f"Result uploaded to: {saveBlob}")

    save_transcript_txt(transcript_raw, path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="path to file in mp3 or mp4, result will have naming convention 'raw'")
    parser.add_argument("-s", "--model-size", default="", help="""transcription model for the selected provider,
                        for example a whisper size such as large for the local-whisper provider,
                        see available models at https://github.com/openai/whisper#available-models-and-languages""")
    args = parser.parse_args()

    main(path=args.path, model_size=args.model_size)
