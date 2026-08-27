"""Selection of the speech-to-text provider for a project.

``stt_provider`` is the current setting. Projects created before it existed
carry the boolean ``whisper_api`` instead, which is migrated here so those
params files keep running unchanged.
"""

from __future__ import annotations

from typing import Any

from .base import SttProvider
from .local_whisper import LocalWhisperSttProvider
from .openai_whisper import OpenAiSttProvider
from .params import parse_legacy_bool

STT_PROVIDER_OPENAI = "openai"
STT_PROVIDER_LOCAL_WHISPER = "local-whisper"

SUPPORTED_STT_PROVIDERS: tuple[str, ...] = (STT_PROVIDER_OPENAI, STT_PROVIDER_LOCAL_WHISPER)
DEFAULT_STT_PROVIDER = STT_PROVIDER_OPENAI

# Spellings accepted from params files, form fields, and env overrides.
STT_PROVIDER_ALIASES: dict[str, str] = {
    "openai": STT_PROVIDER_OPENAI,
    "openai-whisper": STT_PROVIDER_OPENAI,
    "openai_whisper": STT_PROVIDER_OPENAI,
    "whisper-api": STT_PROVIDER_OPENAI,
    "whisper_api": STT_PROVIDER_OPENAI,
    "local-whisper": STT_PROVIDER_LOCAL_WHISPER,
    "local_whisper": STT_PROVIDER_LOCAL_WHISPER,
    "localwhisper": STT_PROVIDER_LOCAL_WHISPER,
    "whisper-local": STT_PROVIDER_LOCAL_WHISPER,
    "whisper": STT_PROVIDER_LOCAL_WHISPER,
    "local": STT_PROVIDER_LOCAL_WHISPER,
}

_PROVIDER_CLASSES: dict[str, type[SttProvider]] = {
    STT_PROVIDER_OPENAI: OpenAiSttProvider,
    STT_PROVIDER_LOCAL_WHISPER: LocalWhisperSttProvider,
}


class UnknownSttProviderError(ValueError):
    """Raised when a project asks for a provider that is not implemented."""


def normalize_stt_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_STT_PROVIDER
    resolved = STT_PROVIDER_ALIASES.get(text)
    if resolved is None:
        supported = ", ".join(SUPPORTED_STT_PROVIDERS)
        raise UnknownSttProviderError(f"Unsupported stt_provider {value!r}; supported providers: {supported}")
    return resolved


def _unwrap(value: Any) -> Any:
    # parameters.json stores {"value": ..., "description": ...} entries.
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _provider_from_source(source: dict[str, Any]) -> str | None:
    configured = _unwrap(source.get("stt_provider"))
    if str(configured or "").strip():
        return normalize_stt_provider(configured)

    legacy = _unwrap(source.get("whisper_api"))
    if legacy is not None and str(legacy).strip() != "":
        return STT_PROVIDER_OPENAI if parse_legacy_bool(legacy) else STT_PROVIDER_LOCAL_WHISPER
    return None


def resolve_stt_provider_name(
    params: dict[str, Any] | None,
    defaults: dict[str, Any] | None = None,
) -> str:
    """Provider for a params file, migrating the legacy ``whisper_api`` flag.

    A project's own setting always wins over the deployment default, and within
    each source ``stt_provider`` wins over the legacy ``whisper_api`` flag.
    """
    for source in (params or {}, defaults or {}):
        name = _provider_from_source(source)
        if name:
            return name
    return DEFAULT_STT_PROVIDER


def legacy_whisper_api_value(provider: str) -> bool:
    """The ``whisper_api`` flag a given provider corresponds to.

    Still written into params so an older worker reading a new project keeps
    picking the same engine.
    """
    return normalize_stt_provider(provider) == STT_PROVIDER_OPENAI


def create_stt_provider(name: str) -> SttProvider:
    provider_name = normalize_stt_provider(name)
    return _PROVIDER_CLASSES[provider_name]()


def provider_for_params(params: dict[str, Any] | None) -> SttProvider:
    return create_stt_provider(resolve_stt_provider_name(params))


def stt_provider_required_env(name: str) -> tuple[str, ...]:
    return _PROVIDER_CLASSES[normalize_stt_provider(name)].required_env
