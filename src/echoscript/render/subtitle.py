from __future__ import annotations

from echoscript.schema import Transcript


def format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours, rem = divmod(milliseconds_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, milliseconds = divmod(rem, 1000)
    sep = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{milliseconds:03d}"


def _segment_text(text: str, speaker: str | None) -> str:
    return f"[{speaker}] {text}" if speaker else text


def render_srt(transcript: Transcript) -> str:
    blocks = []
    for idx, segment in enumerate(transcript.segments, 1):
        blocks.append(
            f"{idx}\n{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}\n"
            f"{_segment_text(segment.text, segment.speaker)}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(transcript: Transcript) -> str:
    blocks = ["WEBVTT"]
    for segment in transcript.segments:
        blocks.append(
            f"{format_timestamp(segment.start, vtt=True)} --> {format_timestamp(segment.end, vtt=True)}\n"
            f"{_segment_text(segment.text, segment.speaker)}"
        )
    return "\n\n".join(blocks) + "\n"
