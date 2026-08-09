from __future__ import annotations

import json

from echoscript.schema import Transcript
from .subtitle import render_srt, render_vtt


def render_transcript(transcript: Transcript, fmt: str) -> str:
    fmt = fmt.lower()
    if fmt == "json":
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
    if not any(segment.speaker for segment in transcript.segments):
        text = transcript.text.strip()
        return text + ("\n" if text else "")

    paragraphs: list[str] = []
    current_speaker: str | None = None
    current_text: list[str] = []

    def flush() -> None:
        nonlocal current_text
        text = " ".join(part.strip() for part in current_text if part.strip()).strip()
        if not text:
            current_text = []
            return
        label = _speaker_label(current_speaker)
        paragraphs.append(f"[{label}] {text}" if label else text)
        current_text = []

    for segment in transcript.segments:
        if current_text and segment.speaker != current_speaker:
            flush()
        current_speaker = segment.speaker
        current_text.append(segment.text)
    flush()
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def _speaker_label(speaker: str | None) -> str | None:
    if not speaker:
        return None
    prefix = "SPEAKER_"
    suffix = speaker[len(prefix) :] if speaker.startswith(prefix) else ""
    if suffix.isdigit():
        return f"Speaker {int(suffix) + 1}"
    return speaker


__all__ = ["render_transcript", "render_srt", "render_vtt"]
