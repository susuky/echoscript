from __future__ import annotations

from pathlib import Path

from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord
from .base import Transcriber


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
        timestamps: bool = True,
        duration: float | None = None,
    ) -> Transcript:
        language = (language or "").strip().lower().replace("_", "-")
        forced_language = None if language in {"", "auto", "none"} else language.split("-")[0]
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            language=forced_language,
            initial_prompt=context.strip() or None,
            hotwords=context.strip() or None,
            multilingual=forced_language is None,
            word_timestamps=timestamps,
            vad_filter=True,
            beam_size=5,
            # As in WhisperX: reduce repetition loops in long recordings.
            # Hotwords retain the supplied glossary when the history is reset.
            condition_on_previous_text=False,
        )
        transcript_segments: list[TranscriptSegment] = []
        text_parts: list[str] = []
        for segment in segments_iter:
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
                )
            )
        return Transcript(
            text=" ".join(part for part in text_parts if part).strip(),
            language=getattr(info, "language", forced_language),
            duration=duration or getattr(info, "duration", None),
            segments=transcript_segments,
            metadata={
                "backend": "faster-whisper",
                "model": self.model_name,
                "compute_type": self.compute_type,
                "condition_on_previous_text": False,
                "multilingual": forced_language is None,
            },
        )
