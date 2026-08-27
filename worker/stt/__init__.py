"""Provider-independent speech-to-text boundary.

Stages after transcription only ever see the canonical transcript defined in
:mod:`stt.schema`, so a new provider can be added without touching them.
"""

from .base import (
    SttCredentialsError,
    SttError,
    SttProvider,
    TranscriptionRequest,
    TranscriptionResult,
)
from .params import (
    parse_legacy_bool,
    stt_param,
    stt_param_bool,
    stt_param_float,
    stt_param_int,
    stt_param_str,
)
from .registry import (
    DEFAULT_STT_PROVIDER,
    STT_PROVIDER_LOCAL_WHISPER,
    STT_PROVIDER_OPENAI,
    SUPPORTED_STT_PROVIDERS,
    UnknownSttProviderError,
    create_stt_provider,
    legacy_whisper_api_value,
    normalize_stt_provider,
    provider_for_params,
    resolve_stt_provider_name,
    stt_provider_required_env,
)
from .schema import (
    PROVIDER_RESPONSE_SCHEMA_VERSION,
    TRANSCRIPT_SCHEMA_VERSION,
    CanonicalTranscript,
    TranscriptSchemaError,
    TranscriptSegment,
    TranscriptWord,
    is_canonical_transcript,
    iter_transcript_words,
    replace_transcript_words,
    validate_transcript,
    words_from_segment_text,
)

__all__ = [
    "CanonicalTranscript",
    "DEFAULT_STT_PROVIDER",
    "PROVIDER_RESPONSE_SCHEMA_VERSION",
    "STT_PROVIDER_LOCAL_WHISPER",
    "STT_PROVIDER_OPENAI",
    "SUPPORTED_STT_PROVIDERS",
    "SttCredentialsError",
    "SttError",
    "SttProvider",
    "TRANSCRIPT_SCHEMA_VERSION",
    "TranscriptSchemaError",
    "TranscriptSegment",
    "TranscriptWord",
    "TranscriptionRequest",
    "TranscriptionResult",
    "UnknownSttProviderError",
    "create_stt_provider",
    "is_canonical_transcript",
    "iter_transcript_words",
    "legacy_whisper_api_value",
    "normalize_stt_provider",
    "parse_legacy_bool",
    "provider_for_params",
    "replace_transcript_words",
    "resolve_stt_provider_name",
    "stt_param",
    "stt_param_bool",
    "stt_param_float",
    "stt_param_int",
    "stt_param_str",
    "stt_provider_required_env",
    "validate_transcript",
    "words_from_segment_text",
]
