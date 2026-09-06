from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from echoscript.schema import Transcript


class Transcriber(ABC):
    @property
    @abstractmethod
    def model_key(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        context: str = "",
        glossary: str = "",
        previous_text: str = "",
        chunk_seconds: float = 60,
        condition_on_previous_text: bool = True,
        context_token_budget: int | None = None,
        glossary_token_budget: int | None = None,
        timestamps: bool = True,
        duration: float | None = None,
    ) -> Transcript:
        raise NotImplementedError
