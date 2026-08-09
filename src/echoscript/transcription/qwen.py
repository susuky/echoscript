from __future__ import annotations

from pathlib import Path

from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord, words_to_segments
from .base import Transcriber


_LANGUAGE_MAP = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "yue": "Cantonese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
}


class QwenTranscriber(Transcriber):
    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-ASR-1.7B",
        *,
        device: str = "cuda",
        timestamps: bool = True,
        aligner_name: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        max_inference_batch_size: int = 8,
        max_new_tokens: int = 2048,
    ):
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:  # pragma: no cover - GPU optional dependency
            raise RuntimeError("Qwen backend requires: pip install 'echoscript[qwen]'") from exc

        self.model_name = model_name
        self.timestamps_enabled = timestamps
        device_map = _device_map(device)
        dtype = _select_dtype(torch, device)
        kwargs = {
            "dtype": dtype,
            "device_map": device_map,
            "max_inference_batch_size": max_inference_batch_size,
            "max_new_tokens": max_new_tokens,
        }
        if timestamps:
            kwargs["forced_aligner"] = aligner_name
            kwargs["forced_aligner_kwargs"] = {
                "dtype": dtype,
                "device_map": device_map,
            }
        self.model = Qwen3ASRModel.from_pretrained(model_name, **kwargs)

    @property
    def model_key(self) -> str:
        suffix = "+aligner" if self.timestamps_enabled else ""
        return f"qwen:{self.model_name}{suffix}"

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        context: str = "",
        timestamps: bool = True,
        duration: float | None = None,
    ) -> Transcript:
        use_timestamps = bool(timestamps and self.timestamps_enabled)
        results = self.model.transcribe(
            audio=str(audio_path),
            context=context,
            language=_normalize_language(language),
            return_time_stamps=use_timestamps,
        )
        result = results[0]
        words: list[TranscriptWord] = []
        if use_timestamps and result.time_stamps is not None:
            for item in result.time_stamps:
                words.append(
                    TranscriptWord(
                        start=float(item.start_time),
                        end=float(item.end_time),
                        text=str(item.text),
                    )
                )

        segments = words_to_segments(words) if words else []
        if not segments and result.text.strip() and duration is not None:
            segments = [TranscriptSegment(start=0.0, end=duration, text=result.text.strip())]

        return Transcript(
            text=result.text.strip(),
            language=result.language or language,
            duration=duration,
            segments=segments,
            metadata={"backend": "qwen", "model": self.model_name, "timestamps": use_timestamps},
        )


def _normalize_language(language: str | None) -> str | None:
    if not language or language.lower() in {"auto", "none"}:
        return None
    return _LANGUAGE_MAP.get(language.lower(), language)


def _device_map(device: str) -> str:
    if device == "cuda":
        return "cuda:0"
    return device


def _select_dtype(torch, device: str):
    if device.startswith("cuda") and torch.cuda.is_available():
        try:
            if torch.cuda.is_bf16_supported():
                return torch.bfloat16
        except (AttributeError, RuntimeError):
            pass
        return torch.float16
    return torch.float32
