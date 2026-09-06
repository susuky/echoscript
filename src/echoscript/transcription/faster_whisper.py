from __future__ import annotations

from pathlib import Path

from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord
from .base import Transcriber
from .prompts import prepare_prompts


class FasterWhisperTranscriber(Transcriber):
    def __init__(
        self,
        model_name: str = "large-v3-turbo",
        *,
        device: str = "cuda",
        compute_type: str = "float16",
    ):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("faster-whisper backend requires: pip install 'echoscript[faster-whisper]'") from exc

        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)

    @property
    def model_key(self) -> str:
        return f"faster-whisper:{self.model_name}:{self.compute_type}"

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
        language = (language or "").strip().lower().replace("_", "-")
        forced_language = None if language in {"", "auto", "none"} else language.split("-")[0]
        initial_prompt, hotwords, prompt_diagnostics = prepare_prompts(
            "faster-whisper", context=context, glossary=glossary,
            previous_text=previous_text if condition_on_previous_text else "",
            tokenizer=getattr(self.model, "hf_tokenizer", None),
            context_token_budget=context_token_budget, glossary_token_budget=glossary_token_budget,
        )
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            language=forced_language,
            initial_prompt=initial_prompt or None,
            hotwords=hotwords or None,
            multilingual=forced_language is None,
            word_timestamps=timestamps,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=condition_on_previous_text,
        )
        transcript_segments: list[TranscriptSegment] = []
        text_parts: list[str] = []
        for index, segment in enumerate(segments_iter):
            text = segment.text.strip()
            text_parts.append(text)
            words: list[TranscriptWord] = []
            if timestamps and segment.words:
                words = [
                    TranscriptWord(
                        start=float(word.start),
                        end=float(word.end),
                        text=word.word,
                        confidence=float(word.probability),
                    )
                    for word in segment.words
                ]
            transcript_segments.append(
                TranscriptSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=text,
                    words=words,
                    id=f"segment-{index:04d}",
                    raw_text=text,
                    alignment="available" if timestamps else "disabled",
                    diagnostics={
                        name: getattr(segment, name)
                        for name in ("id", "seek", "tokens", "avg_logprob", "compression_ratio",
                                     "no_speech_prob", "temperature")
                        if hasattr(segment, name)
                    },
                )
            )
        return Transcript(
            text=" ".join(part for part in text_parts if part).strip(),
            language=getattr(info, "language", forced_language),
            duration=duration if duration is not None else getattr(info, "duration", None),
            raw_text=" ".join(part for part in text_parts if part).strip(),
            segments=transcript_segments,
            metadata={
                "backend": "faster-whisper",
                "model": self.model_name,
                "compute_type": self.compute_type,
                "condition_on_previous_text": condition_on_previous_text,
                "multilingual": forced_language is None,
                "timestamps": timestamps and bool(transcript_segments),
                "prompt": prompt_diagnostics,
                "model_diagnostics": {
                    name: getattr(info, name)
                    for name in ("language_probability", "duration", "duration_after_vad", "all_language_probs")
                    if hasattr(info, name)
                },
            },
        )
