from __future__ import annotations

import math
import unicodedata
from pathlib import Path

from echoscript.schema import Transcript, TranscriptWord, words_to_segments
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
    "ar": "Arabic",
    "id": "Indonesian",
    "th": "Thai",
    "vi": "Vietnamese",
    "tr": "Turkish",
    "hi": "Hindi",
    "ms": "Malay",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "fil": "Filipino",
    "tl": "Filipino",
    "fa": "Persian",
    "el": "Greek",
    "hu": "Hungarian",
    "mk": "Macedonian",
    "ro": "Romanian",
}
_ALIGNER_LANGUAGES = {
    "Chinese", "English", "Cantonese", "French", "German", "Italian",
    "Japanese", "Korean", "Portuguese", "Russian", "Spanish",
}


class QwenTranscriber(Transcriber):
    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-ASR-1.7B",
        *,
        device: str = "cuda",
        timestamps: bool = True,
        aligner_name: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        max_inference_batch_size: int = 1,
        max_new_tokens: int = 4096,
    ):
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:  # pragma: no cover - GPU optional dependency
            raise RuntimeError("Qwen backend requires the qwen-asr package") from exc

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
        forced_language = _normalize_language(language)
        use_timestamps = bool(timestamps and self.timestamps_enabled)
        if forced_language and forced_language not in _ALIGNER_LANGUAGES:
            use_timestamps = False
        audio: object = str(audio_path)
        offsets = [0.0]
        if duration is not None and duration > 60:
            # Reuse low-energy cuts to bound GPU attention memory for long media.
            from qwen_asr.inference.utils import (
                SAMPLE_RATE, normalize_audios, split_audio_into_chunks,
            )

            waveform = normalize_audios(str(audio_path))[0]
            chunks = split_audio_into_chunks(waveform, SAMPLE_RATE, 60)
            audio = [(chunk, SAMPLE_RATE) for chunk, _ in chunks]
            offsets = [offset for _, offset in chunks]
        results = self.model.transcribe(
            audio=audio,
            context=context.strip(),
            language=forced_language,
            return_time_stamps=use_timestamps,
        )
        if not results or len(results) != len(offsets):
            raise RuntimeError("Qwen returned an incomplete transcription result")
        text = "\n".join(item.text.strip() for item in results if item.text.strip())
        detected_languages = list(dict.fromkeys(item.language for item in results if item.language))
        detected_language = ",".join(detected_languages) or forced_language
        words: list[TranscriptWord] = []
        alignment = {"status": "disabled"}
        if timestamps:
            alignment = {"status": "unavailable", "reason": "missing_timestamps"}
        if timestamps and any(
            part not in _ALIGNER_LANGUAGES
            for part in (detected_language or "").split(",")
        ):
            use_timestamps = False
            alignment = {"status": "unavailable", "reason": "unsupported_language"}
        if use_timestamps:
            reason = "missing_timestamps"
            for index, (result, offset) in enumerate(zip(results, offsets)):
                if not result.text.strip():
                    continue
                if result.time_stamps is None:
                    words = []
                    reason = "missing_timestamps"
                    break
                chunk_duration = (offsets[index + 1] - offset if index + 1 < len(offsets)
                                  else duration - offset if duration is not None else None)
                chunk_words, reason = _restore_words(result.text.strip(), result.time_stamps, chunk_duration)
                if not chunk_words:
                    words = []
                    break
                if words and chunk_words[0].start + offset < words[-1].end:
                    words = []
                    reason = "invalid_timestamps"
                    break
                if words:
                    chunk_words[0].text = "\n" + chunk_words[0].text
                for word in chunk_words:
                    word.start += offset
                    word.end += offset
                words.extend(chunk_words)
            alignment = (
                {"status": "available"}
                if words else {"status": "unavailable", "reason": reason}
            )

        segments = words_to_segments(words, preserve_spacing=True)

        return Transcript(
            text=text,
            language=detected_language,
            duration=duration,
            segments=segments,
            metadata={
                "backend": "qwen", "model": self.model_name,
                "timestamps": bool(words), "alignment": alignment,
                "word_text_verbatim": True,
            },
        )


def _normalize_language(language: str | None) -> str | None:
    value = (language or "").strip().lower().replace("_", "-")
    if not value or value in {"auto", "none"}:
        return None
    normalized = _LANGUAGE_MAP.get(value, _LANGUAGE_MAP.get(value.split("-")[0], value.title()))
    if normalized not in _LANGUAGE_MAP.values():
        raise ValueError(f"Unsupported Qwen language: {language}")
    return normalized


def _kept_character(char: str) -> bool:
    # Qwen's aligner removes punctuation, symbols and combining marks. Use this
    # only to match positions; all displayed text comes from the untouched ASR.
    return char == "'" or unicodedata.category(char)[0] in {"L", "N"}


def _restore_words(text: str, timestamps, duration: float | None) -> tuple[list[TranscriptWord], str]:
    items = list(timestamps)
    positions = [index for index, char in enumerate(text) if _kept_character(char)]
    source = "".join(text[index] for index in positions)
    tokens = ["".join(char for char in str(item.text) if _kept_character(char)) for item in items]
    if not source or not tokens or any(not token for token in tokens) or "".join(tokens) != source:
        return [], "text_mismatch"

    words: list[TranscriptWord] = []
    offset = 0
    boundary = 0
    previous_end = 0.0
    for item, token in zip(items, tokens):
        try:
            start, end = float(item.start_time), float(item.end_time)
        except (TypeError, ValueError, OverflowError):
            return [], "invalid_timestamps"
        if (
            not math.isfinite(start) or not math.isfinite(end)
            or start < previous_end or end < start
            or (duration is not None and end > duration + 0.1)
        ):
            return [], "invalid_timestamps"
        offset += len(token)
        next_boundary = positions[offset] if offset < len(positions) else len(text)
        # Keep opening punctuation and whitespace with the following word, while
        # sentence punctuation and combining marks stay with the preceding one.
        separator = text[positions[offset - 1] + 1:next_boundary]
        for index, char in enumerate(separator):
            if char.isspace() or unicodedata.category(char) in {"Ps", "Pi", "Sc"}:
                next_boundary = positions[offset - 1] + 1 + index
                break
        if offset == len(positions):
            next_boundary = len(text)
        words.append(TranscriptWord(start, end, text[boundary:next_boundary]))
        boundary = next_boundary
        previous_end = end
    if not any(word.end > word.start for word in words):
        return [], "invalid_timestamps"
    return words, ""


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
