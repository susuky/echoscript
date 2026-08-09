from __future__ import annotations

import gc

from echoscript.diarization import PyannoteDiarizer
from echoscript.schema import JobOptions
from echoscript.transcription import FasterWhisperTranscriber, QwenTranscriber, Transcriber


class ModelManager:
    """Owns GPU model objects inside the worker process only."""

    def __init__(self, *, hf_token: str | None = None):
        self.hf_token = hf_token
        self._asr: Transcriber | None = None
        self._asr_key: str | None = None
        self._diarizer: PyannoteDiarizer | None = None
        self._diarizer_key: str | None = None

    def get_transcriber(self, options: JobOptions) -> Transcriber:
        key = self._desired_asr_key(options)
        if self._asr is not None and self._asr_key == key:
            return self._asr
        self.drop_asr()
        if options.asr_backend == "qwen":
            self._asr = QwenTranscriber(
                options.asr_model,
                device=options.device,
                timestamps=options.timestamps,
            )
        elif options.asr_backend in {"faster-whisper", "faster_whisper", "whisper"}:
            self._asr = FasterWhisperTranscriber(
                options.asr_model,
                device=options.device,
                compute_type=options.compute_type,
            )
        else:
            raise ValueError(f"Unsupported ASR backend: {options.asr_backend}")
        self._asr_key = key
        return self._asr

    def get_diarizer(self, options: JobOptions) -> PyannoteDiarizer:
        key = f"pyannote:pyannote/speaker-diarization-community-1:{options.device}"
        if self._diarizer is not None and self._diarizer_key == key:
            return self._diarizer
        self.drop_diarizer()
        self._diarizer = PyannoteDiarizer(token=self.hf_token, device=options.device)
        self._diarizer_key = key
        return self._diarizer

    def drop_asr(self) -> None:
        if self._asr is not None:
            self._asr = None
            self._asr_key = None
            _best_effort_cuda_cleanup()

    def drop_diarizer(self) -> None:
        if self._diarizer is not None:
            self._diarizer = None
            self._diarizer_key = None
            _best_effort_cuda_cleanup()

    def unload_all(self) -> None:
        self._asr = None
        self._diarizer = None
        self._asr_key = None
        self._diarizer_key = None
        _best_effort_cuda_cleanup()

    @staticmethod
    def _desired_asr_key(options: JobOptions) -> str:
        timestamps = "+aligner" if options.asr_backend == "qwen" and options.timestamps else ""
        return f"{options.asr_backend}:{options.asr_model}:{options.compute_type}:{options.device}{timestamps}"


def _best_effort_cuda_cleanup() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except (ImportError, RuntimeError):
        pass
