from __future__ import annotations

from abc import ABC, abstractmethod

from echoscript.schema import Transcript


class Renderer(ABC):
    @abstractmethod
    def render(self, transcript: Transcript) -> str:
        raise NotImplementedError
