from __future__ import annotations

import tempfile
from pathlib import Path

from echoscript.media import download_public_url
from echoscript.render.subtitle import format_timestamp as _format_timestamp
from echoscript.schema import Transcript, TranscriptSegment
from echoscript.render import render_transcript


def format_timestamp(t: float, template: str = "{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}") -> str:
    if not template:
        return ""
    milliseconds_total = max(0, round(t * 1000))
    hours, rem = divmod(milliseconds_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, milliseconds = divmod(rem, 1000)
    return template.format(hours=hours, minutes=minutes, seconds=seconds, milliseconds=milliseconds)


def segments2subtitle(segments, fmt: str = "srt") -> str:
    transcript = Transcript(
        text=" ".join(str(segment.get("text", "")).strip() for segment in segments).strip(),
        segments=[
            TranscriptSegment(
                start=float(segment["start"]),
                end=float(segment["end"]),
                text=str(segment.get("text", "")).strip(),
            )
            for segment in segments
        ],
    )
    return render_transcript(transcript, fmt)


def get_yt_audio(url: str, output_path: str = "~/.echoscript/tmp", filename: str = "tmp") -> str:
    """Deprecated compatibility helper; use media.download_public_url instead."""
    path = download_public_url(url, Path(output_path).expanduser())
    return str(path)


class classproperty(property):
    def __get__(self, cls, owner):
        return self.fget(owner)
