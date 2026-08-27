"""Provider-independent speech-to-text interface."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from .schema import CanonicalTranscript, PROVIDER_RESPONSE_SCHEMA_VERSION


class SttError(RuntimeError):
    """Base class for provider failures surfaced to the pipeline."""


class SttCredentialsError(SttError):
    """A required credential for the selected provider is missing."""


@dataclass
class TranscriptionRequest:
    """Everything a provider needs, with no provider-specific fields."""

    audio_path: Path
    params: dict[str, Any] = field(default_factory=dict)
    language: str | None = None
    work_dir: Path | None = None
    logger: logging.Logger | None = None

    def __post_init__(self) -> None:
        self.audio_path = Path(self.audio_path)
        if self.work_dir is not None:
            self.work_dir = Path(self.work_dir)

    def get_logger(self) -> logging.Logger:
        return self.logger or logging.getLogger("podocracy.stt")

    def chunk_dir(self) -> Path:
        base = self.work_dir or self.audio_path.parent
        directory = Path(base) / "stt-chunks"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


@dataclass
class TranscriptionResult:
    """Normalized transcript plus the untouched payload the provider returned."""

    transcript: CanonicalTranscript
    provider_response: dict[str, Any]


class SttProvider(ABC):
    """Common interface every speech-to-text provider implements."""

    name: ClassVar[str]
    # Environment variables that must be set before this provider can run.
    required_env: ClassVar[tuple[str, ...]] = ()

    @abstractmethod
    def resolve_model(self, params: dict[str, Any]) -> str:
        """Model identifier recorded as transcript provenance."""

    @abstractmethod
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Transcribe audio into a canonical transcript."""

    def missing_credentials(self) -> list[str]:
        return [name for name in self.required_env if not (os.getenv(name) or "").strip()]

    def ensure_credentials(self) -> None:
        missing = self.missing_credentials()
        if missing:
            joined = ", ".join(missing)
            raise SttCredentialsError(f"{joined} is not set, required by the '{self.name}' transcription provider")

    def wrap_provider_response(self, model: str, payload: Any) -> dict[str, Any]:
        """Envelope for the untouched provider payload.

        Only provenance is added around it; the payload itself is stored exactly
        as the provider returned it so it stays useful for debugging and replay.
        """
        return {
            "schema_version": PROVIDER_RESPONSE_SCHEMA_VERSION,
            "provider": self.name,
            "model": model,
            "response": payload,
        }
