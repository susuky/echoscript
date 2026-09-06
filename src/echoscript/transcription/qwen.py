from __future__ import annotations

import math
from copy import deepcopy
import unicodedata
from pathlib import Path

from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord, words_to_segments
from .base import Transcriber
from .prompts import prepare_prompts


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
        self.aligner_name = aligner_name
        self.aligner_kwargs = {"dtype": dtype, "device_map": device_map}
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
        glossary: str = "",
        previous_text: str = "",
        chunk_seconds: float = 60,
        condition_on_previous_text: bool = True,
        context_token_budget: int | None = None,
        glossary_token_budget: int | None = None,
        timestamps: bool = True,
        duration: float | None = None,
    ) -> Transcript:
        forced_language = _normalize_language(language)
        tokenizer = getattr(getattr(self.model, "processor", None), "tokenizer", None)
        audio_parts: list[tuple[object, float, float | None]] = [(str(audio_path), 0.0, duration)]
        if duration is not None and duration > chunk_seconds:
            from qwen_asr.inference.utils import SAMPLE_RATE, normalize_audios, split_audio_into_chunks

            waveform = normalize_audios(str(audio_path))[0]
            chunks = split_audio_into_chunks(waveform, SAMPLE_RATE, chunk_seconds)
            audio_parts = [
                ((chunk, SAMPLE_RATE), offset,
                 chunks[index + 1][1] if index + 1 < len(chunks) else duration)
                for index, (chunk, offset) in enumerate(chunks)
            ]
        segments: list[TranscriptSegment] = []
        detected_languages: list[str] = []
        prompt_diagnostics: list[dict] = []
        for index, (audio, start, end) in enumerate(audio_parts):
            prompt, _, prompt_info = prepare_prompts(
                "qwen", context=context, glossary=glossary,
                previous_text=previous_text if condition_on_previous_text else "", tokenizer=tokenizer,
                context_token_budget=context_token_budget, glossary_token_budget=glossary_token_budget,
            )
            prompt_diagnostics.append(prompt_info)
            # ASR must finish independently. An aligner exception must never
            # discard successfully decoded text or force the ASR to run again.
            try:
                results = self.model.transcribe(
                    audio=audio, context=prompt, language=forced_language, return_time_stamps=False,
                )
                if not results or len(results) != 1:
                    raise RuntimeError("Qwen returned an incomplete transcription result")
                result = results[0]
                text = result.text.strip()
                result_language = result.language or forced_language
                diagnostics = {
                    "language": result_language,
                    "raw_model_text": result.text,
                    "generation_limit": getattr(self.model, "max_new_tokens", None),
                    "generation_diagnostics_available": False,
                }
                # Current qwen-asr's public result omits raw decoded output and
                # stop reasons. Preserve these if a newer provider exposes them.
                for name in ("raw_text", "raw_output", "finish_reason", "generated_tokens", "token_ids"):
                    value = getattr(result, name, None)
                    if isinstance(value, (str, int, float, list, dict)):
                        diagnostics[name] = value
                        diagnostics["generation_diagnostics_available"] = True
                if not isinstance(diagnostics["generation_limit"], (int, type(None))):
                    diagnostics["generation_limit"] = None
                if result_language and result_language not in detected_languages:
                    detected_languages.append(result_language)
                if text:
                    previous_text = text
            except Exception as exc:
                if len(audio_parts) == 1:
                    raise
                text = ""
                diagnostics = {"asr_error": type(exc).__name__, "asr_error_message": str(exc)}
            segments.append(TranscriptSegment(
                start=start, end=end if end is not None else start, text=text,
                raw_text=text, id=f"segment-{index:04d}", alignment="disabled",
                diagnostics={**diagnostics, "audio_start": start, "audio_end": end},
            ))
        text = "\n".join(segment.text for segment in segments if segment.text)
        transcript = Transcript(
            text=text, raw_text=text, language=",".join(detected_languages) or forced_language,
            duration=duration, segments=segments,
            metadata={"backend": "qwen", "model": self.model_name, "timestamps": False,
                      "alignment": {"status": "disabled"}, "word_text_verbatim": True,
                      "condition_on_previous_text": condition_on_previous_text,
                      "prompt": prompt_diagnostics},
        )
        return self.align(audio_path, transcript, duration=duration) if timestamps else transcript

    def release_aligner(self) -> None:
        """Release the prior alignment pass before recognizing another recording."""
        if getattr(self.model, "forced_aligner", None) is not None:
            self.model.forced_aligner = None
            from echoscript.worker.model_manager import _best_effort_cuda_cleanup

            _best_effort_cuda_cleanup()

    def align(self, audio_path: str | Path, transcript: Transcript, *,
              duration: float | None = None) -> Transcript:
        """Align saved ASR segments; failures retain text and its audio envelope."""
        output = deepcopy(transcript)
        duration = duration if duration is not None else transcript.duration
        source_segments = output.segments or [TranscriptSegment(
            0, duration or 0, output.text, raw_text=output.raw_text or output.text,
            id="segment-0000", alignment="disabled",
        )]
        aligned_segments: list[TranscriptSegment] = []
        gaps: list[dict] = []
        waveform = None
        aligner_load_error: Exception | None = None
        aligner_load_attempted = False
        for index, segment in enumerate(source_segments):
            if segment.alignment == "available" and segment.words:
                aligned_segments.append(segment)
                continue
            start = segment.diagnostics.get("audio_start", segment.start)
            end = segment.diagnostics.get("audio_end", segment.end if segment.end > start else duration)
            segment_duration = end - start if end is not None else None
            raw_text = segment.raw_text if segment.raw_text is not None else segment.text
            languages = (segment.diagnostics.get("language") or output.language or "").split(",")
            # Japanese tokenization also keeps mixed Latin/CJK text. Retain this
            # decision in diagnostics rather than pretending mixed text is monolingual.
            align_language = "Japanese" if "Japanese" in languages else languages[0]
            reason = ""
            words: list[TranscriptWord] = []
            if not raw_text.strip():
                reason = "asr_failed" if segment.diagnostics.get("asr_error") else "no_transcribed_text"
            elif any(language not in _ALIGNER_LANGUAGES for language in languages):
                reason = "unsupported_language"
            elif not self.timestamps_enabled:
                reason = "aligner_disabled"
            else:
                try:
                    if getattr(self.model, "forced_aligner", None) is None:
                        if not aligner_load_attempted:
                            aligner_load_attempted = True
                            try:
                                from qwen_asr import Qwen3ForcedAligner

                                self.model.forced_aligner = Qwen3ForcedAligner.from_pretrained(
                                    self.aligner_name, **self.aligner_kwargs,
                                )
                            except Exception as exc:
                                aligner_load_error = exc
                        if aligner_load_error is not None:
                            raise aligner_load_error
                    audio: object = str(audio_path)
                    if len(source_segments) > 1 or start > 0:
                        from qwen_asr.inference.utils import SAMPLE_RATE, normalize_audios

                        if waveform is None:
                            waveform = normalize_audios(str(audio_path))[0]
                        audio = (waveform[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE) if end is not None else None], SAMPLE_RATE)
                    results = self.model.forced_aligner.align(audio=audio, text=raw_text, language=align_language)
                    if not results or len(results) != 1 or results[0] is None:
                        reason = "missing_timestamps"
                    else:
                        words, reason = _restore_words(raw_text, results[0], segment_duration)
                except Exception as exc:
                    reason = "alignment_failed"
                    segment.diagnostics.update(alignment_error=type(exc).__name__, alignment_error_message=str(exc))
            if words:
                for word in words:
                    word.start += start
                    word.end += start
                if aligned_segments:
                    words[0].text = "\n" + words[0].text
                children = words_to_segments(words, preserve_spacing=True)
                for child_index, child in enumerate(children):
                    child.id = f"{segment.id or f'segment-{index:04d}'}-{child_index:03d}"
                    child.raw_text = child.text
                    child.alignment = "available"
                    child.diagnostics = {key: value for key, value in segment.diagnostics.items()
                                         if key not in {"alignment_reason", "alignment_error", "alignment_error_message"}}
                    child.diagnostics["alignment_language"] = align_language
                aligned_segments.extend(children)
            else:
                segment.words = []
                segment.speaker = None
                segment.alignment = "unavailable"
                segment.diagnostics["alignment_reason"] = reason
                aligned_segments.append(segment)
                gaps.append({"segment_id": segment.id, "start": start, "end": end, "reason": reason})
        output.segments = aligned_segments
        available = any(segment.alignment == "available" for segment in aligned_segments)
        alignment = {"status": "partial" if available and gaps else "available" if available else "unavailable"}
        if gaps:
            alignment["gaps"] = gaps
            if len({gap["reason"] for gap in gaps}) == 1:
                alignment["reason"] = gaps[0]["reason"]
        output.metadata.update(timestamps=available, alignment=alignment)
        return output


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
