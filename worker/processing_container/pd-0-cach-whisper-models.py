

import whisper ## TODO: check if whisper is installed, if not, install it as in pd-010-raw-transcribe.py

def load_whisper_models(model_sizes):
    models = {}
    for model_size in model_sizes:
        print(f"Loading model: {model_size}")
        models[model_size] = whisper.load_model(model_size)
        print(f"Model loaded: {model_size}")
    return models


def main(model_sizes):
    load_whisper_models(model_sizes)

if __name__ == "__main__":
    # we will download base model only to save space in ACR for now
    # model_sizes = ["base.en", "large"]
    model_sizes = ["base.en"]
    main(model_sizes)

