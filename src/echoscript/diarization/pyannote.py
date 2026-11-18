from __future__ import annotations

from pathlib import Path

from echoscript.schema import SpeakerTurn


class PyannoteDiarizer:
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

        self.pipeline = Pipeline.from_pretrained(model_name, token=token)
        if device.startswith("cuda") and torch.cuda.is_available():
            self.pipeline.to(torch.device(device))

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
        # Community-1 provides an exclusive annotation. Legacy pipelines return the annotation itself.
        annotation = getattr(output, "exclusive_speaker_diarization", output)
        return [SpeakerTurn(float(turn.start), float(turn.end), str(speaker)) for turn, speaker in annotation]
