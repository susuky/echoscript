from __future__ import annotations

import warnings

from echoscript.render import render_transcript
from echoscript.transcription import FasterWhisperTranscriber, QwenTranscriber
from echoscript.utils import classproperty


class Audio2Text:
    """Deprecated synchronous compatibility API.

    The web application keeps model lifecycle inside a local GPU worker process.
    """

    available_models = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo", "turbo"]
    available_formats = ("json", "vtt", "srt", "txt", None)

    @classproperty
    def available_languages(cls) -> dict[str, str]:
        """Return Whisper language codes mapped to display names."""
        from transformers.models.whisper.tokenization_whisper import LANGUAGES

        languages = dict(LANGUAGES)
        languages["zh-tw"] = "Taiwan"
        return dict(
            sorted(
                ((code, name.capitalize()) for code, name in languages.items()),
                key=lambda item: item[1],
            )
        )

    @classmethod
    def is_language_available(cls, language: str) -> bool:
        if not isinstance(language, str):
            return False
        normalized = language.strip().lower()
        return normalized in cls.available_languages or normalized in {
            name.lower() for name in cls.available_languages.values()
        }

    @classmethod
    def load_whisper_model(cls, model_name: str = "base"):
        """Load an openai-whisper model for legacy callers."""
        try:
            import whisper
        except ImportError as exc:  # pragma: no cover - legacy optional dependency
            raise RuntimeError("Legacy model loading requires the optional package: pip install openai-whisper") from exc

        if model_name not in whisper.available_models():
            raise ValueError(f"Whisper model `{model_name}` is not available.")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            return whisper.load_model(model_name)

    def transcribe(
        self,
        audio,
        model_name: str = "large-v3-turbo",
        fmt: str | None = None,
        language: str | None = None,
        *,
        backend: str = "faster-whisper",
        **kwargs,
    ):
        if language is not None and not self.is_language_available(language):
            raise ValueError(f"Language `{language}` is not available.")
        if fmt not in self.available_formats:
            raise ValueError(f"Format `{fmt}` is not supported.")

        warnings.warn(
            "Audio2Text is a compatibility API. Use the web application for isolated GPU workers.",
            DeprecationWarning,
            stacklevel=2,
        )
        timestamps = bool(kwargs.pop("timestamps", True))
        context = str(kwargs.pop("context", ""))
        duration = kwargs.pop("duration", None)
        device = str(kwargs.pop("device", "cuda"))
        language_code = self._language_code(language)

        if backend == "qwen":
            aligner_name = kwargs.pop("aligner_name", "Qwen/Qwen3-ForcedAligner-0.6B")
            max_inference_batch_size = int(kwargs.pop("max_inference_batch_size", 8))
            max_new_tokens = int(kwargs.pop("max_new_tokens", 2048))
            self._reject_unknown_options(kwargs)
            transcriber = QwenTranscriber(
                model_name,
                device=device,
                timestamps=timestamps,
                aligner_name=aligner_name,
                max_inference_batch_size=max_inference_batch_size,
                max_new_tokens=max_new_tokens,
            )
        elif backend in {"faster-whisper", "faster_whisper", "whisper"}:
            compute_type = str(kwargs.pop("compute_type", "float16"))
            self._reject_unknown_options(kwargs)
            transcriber = FasterWhisperTranscriber(
                model_name,
                device=device,
                compute_type=compute_type,
            )
        else:
            raise ValueError(f"Unsupported ASR backend: {backend}")

        transcript = transcriber.transcribe(
            audio,
            language=language_code,
            context=context,
            timestamps=timestamps,
            duration=duration,
        )
        if fmt == "json":
            return transcript.to_dict()
        if fmt in {"srt", "vtt", "txt"}:
            return render_transcript(transcript, fmt)
        return transcript.text

    @staticmethod
    def _reject_unknown_options(options: dict) -> None:
        if options:
            names = ", ".join(sorted(options))
            raise TypeError(f"Unexpected transcription option(s): {names}")

    @classmethod
    def _language_code(cls, language: str | None) -> str | None:
        if language is None:
            return None
        normalized = language.strip().lower()
        if normalized in cls.available_languages:
            return normalized
        for code, name in cls.available_languages.items():
            if normalized == name.lower():
                return code
        return language


def audio2text(audio, model_name="large-v3-turbo", fmt=None, language=None, **kwargs):
    return Audio2Text().transcribe(audio, model_name=model_name, fmt=fmt, language=language, **kwargs)
