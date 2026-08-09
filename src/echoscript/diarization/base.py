from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from echoscript.schema import SpeakerTurn


class Diarizer(ABC):
    @property
    @abstractmethod
    def model_key(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def diarize(
        self,
        audio_path: str | Path,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list[SpeakerTurn]:
        raise NotImplementedError
