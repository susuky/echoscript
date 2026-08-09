from __future__ import annotations

from pathlib import Path

from echoscript.schema import SpeakerTurn
from .base import Diarizer


class PyannoteDiarizer(Diarizer):
    def __init__(
        self,
        model_name: str = "pyannote/speaker-diarization-community-1",
        *,
        token: str | None = None,
        device: str = "cuda",
    ):
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("Diarization requires: pip install 'echoscript[diarization]'") from exc

        self.model_name = model_name
        self.pipeline = Pipeline.from_pretrained(model_name, token=token)
        if device.startswith("cuda") and torch.cuda.is_available():
            self.pipeline.to(torch.device(device))

    @property
    def model_key(self) -> str:
        return f"pyannote:{self.model_name}"

    def diarize(
        self,
        audio_path: str | Path,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list[SpeakerTurn]:
        kwargs = {}
        if min_speakers is not None:
            kwargs["min_speakers"] = min_speakers
        if max_speakers is not None:
            kwargs["max_speakers"] = max_speakers
        output = self.pipeline(str(audio_path), **kwargs)
        annotation = getattr(output, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(output, "speaker_diarization", output)

        turns: list[SpeakerTurn] = []
        try:
            iterator = annotation.itertracks(yield_label=True)
            for turn, _, speaker in iterator:
                turns.append(SpeakerTurn(float(turn.start), float(turn.end), str(speaker)))
        except AttributeError:
            # pyannote 4's public examples also support two-value iteration.
            for turn, speaker in annotation:
                turns.append(SpeakerTurn(float(turn.start), float(turn.end), str(speaker)))
        return turns
