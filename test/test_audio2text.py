import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from echoscript.audio2text import Audio2Text, audio2text
from echoscript.schema import Transcript, TranscriptSegment


def test_languages_and_validation_do_not_require_openai_whisper(monkeypatch):
    monkeypatch.setitem(sys.modules, "whisper", None)
    assert Audio2Text.available_languages["en"] == "English"
    assert Audio2Text.available_languages["zh-tw"] == "Taiwan"
    assert Audio2Text.is_language_available(" English ")
    assert Audio2Text.is_language_available("en")
    assert not Audio2Text.is_language_available("invalid_language")
    with pytest.raises(RuntimeError, match="pip install openai-whisper"):
        Audio2Text.load_whisper_model("tiny")


def test_legacy_model_loader_validates_before_loading(monkeypatch):
    model = object()
    load = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(
        available_models=lambda: ["tiny"], load_model=load,
    ))
    assert Audio2Text.load_whisper_model("tiny") is model
    with pytest.raises(ValueError, match="is not available"):
        Audio2Text.load_whisper_model("invalid_model")
    load.assert_called_once_with("tiny")


def test_transcribe_normalizes_language_and_renders_backend_output(monkeypatch):
    transcript = Transcript(text="This is an example", segments=[
        TranscriptSegment(start=0, end=2, text="This is an example"),
    ])
    transcriber = Mock()
    transcriber.transcribe.return_value = transcript
    factory = Mock(return_value=transcriber)
    monkeypatch.setattr(sys.modules["echoscript.audio2text"], "FasterWhisperTranscriber", factory)
    for fmt in ("srt", "vtt", "txt", None, "json"):
        with pytest.warns(DeprecationWarning):
            result = audio2text("example.wav", fmt=fmt, language="English", model_name="tiny")
        if fmt == "json":
            assert result["text"] == transcript.text
        else:
            assert transcript.text in result
        if fmt == "srt":
            assert "00:00:00,000 --> 00:00:02,000" in result
    transcriber.transcribe.assert_called_with(
        "example.wav", language="en", context="", timestamps=True, duration=None,
    )
    factory.assert_called_with("tiny", device="cuda", compute_type="float16")
    with pytest.raises(ValueError, match="Language"):
        audio2text("example.wav", language="invalid_language")
    with pytest.raises(ValueError, match="Format"):
        audio2text("example.wav", fmt="invalid_format")
