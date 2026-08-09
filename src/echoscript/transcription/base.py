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
        timestamps: bool = True,
        duration: float | None = None,
    ) -> Transcript:
        raise NotImplementedError
