from __future__ import annotations

import json

from echoscript.schema import Transcript
from echoscript.pipeline.normalize import segment_text_spans
from .subtitle import render_srt, render_vtt, validate_subtitles


def render_transcript(transcript: Transcript, fmt: str) -> str:
    fmt = fmt.lower()
    if fmt == "json":
        validate_subtitles(transcript)
        return json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2)
    if fmt == "txt":
        return _render_txt(transcript)
    if fmt == "srt":
        return render_srt(transcript)
    if fmt == "vtt":
        return render_vtt(transcript)
    raise ValueError(f"Unsupported output format: {fmt}")


def _render_txt(transcript: Transcript) -> str:
    """Render speaker-labelled paragraphs when diarization is available."""
    spans = segment_text_spans(transcript)
    if not any(segment.speaker for segment in transcript.segments) or spans is None:
        text = transcript.text.strip()
        return text + ("\n" if text else "")

    paragraphs: list[str] = []
    current_speaker: str | None = None
    current_text: list[str] = []

    def flush() -> None:
        nonlocal current_text
        text = "".join(current_text).strip()
        if not text:
            current_text = []
            return
        label = _speaker_label(current_speaker)
        paragraphs.append(f"[{label}] {text}" if label else text)
        current_text = []

    cursor = 0
    for segment, (start, end) in zip(transcript.segments, spans):
        separator = transcript.text[cursor:start]
        if separator.strip():
            flush()
            paragraphs.append(separator.strip())
            separator = ""
        if current_text and segment.speaker != current_speaker:
            flush()
        current_speaker = segment.speaker
        current_text.append(separator + transcript.text[start:end])
        cursor = end
    flush()
    if transcript.text[cursor:].strip():
        paragraphs.append(transcript.text[cursor:].strip())
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def _speaker_label(speaker: str | None) -> str | None:
    if not speaker:
        return None
    prefix = "SPEAKER_"
    suffix = speaker[len(prefix) :] if speaker.startswith(prefix) else ""
    if suffix.isdigit():
        return f"Speaker {int(suffix) + 1}"
    return speaker


__all__ = ["render_transcript", "render_srt", "render_vtt", "validate_subtitles"]
