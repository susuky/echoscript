from __future__ import annotations

import math
from html import escape
import unicodedata

from echoscript.schema import Transcript, TranscriptSegment


def format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Subtitle timestamps must be finite and nonnegative")
    milliseconds_total = round(seconds * 1000)
    hours, rem = divmod(milliseconds_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, milliseconds = divmod(rem, 1000)
    sep = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{milliseconds:03d}"


def validate_subtitles(transcript: Transcript) -> list[TranscriptSegment]:
    """Keep usable cues and report every omitted span without inventing timing.

    Hard gates are 100 ms, 60 non-space characters/second and 160 characters
    per cue. Rates above 25 characters/second remain available with a warning;
    these are conservative integrity limits, not a language quality score.
    """
    accepted = []
    gaps = []
    warnings = []
    previous_end = 0
    cursor = 0
    covered_chars = 0
    for index, segment in enumerate(transcript.segments):
        errors = []
        cue_id = segment.id or str(index)
        text = segment.text.strip()
        start = transcript.text.find(text, cursor) if text else cursor
        if start < 0:
            errors.append("canonical_text_mismatch")
        else:
            missing = transcript.text[cursor:start]
            if missing.strip():
                gaps.append({"id": None, "start": None, "end": None, "text": missing,
                             "reasons": ["missing_segment_text"]})
            cursor = start + len(text)
        if segment.alignment != "available":
            errors.append("alignment_" + segment.alignment)
        if not any(unicodedata.category(char)[0] not in {"C", "Z"} for char in text):
            errors.append("empty_text")
        if any(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in text):
            errors.append("unsupported_control_character")
        times_valid = all(isinstance(t, (float, int)) and math.isfinite(t) for t in (segment.start, segment.end))
        if not times_valid:
            errors.append("nonfinite_timestamps")
        else:
            start_ms, end_ms = round(segment.start * 1000), round(segment.end * 1000)
            duration_ms = end_ms - start_ms
            if segment.start < 0:
                errors.append("negative_timestamp")
            if duration_ms <= 0:
                errors.append("nonpositive_duration")
            elif duration_ms < 100:
                errors.append("too_short")
            if start_ms < previous_end:
                errors.append("overlap_or_out_of_order")
            if transcript.duration is not None and math.isfinite(transcript.duration) and end_ms > round(transcript.duration * 1000):
                errors.append("outside_audio")
            chars = sum(not char.isspace() for char in text)
            rate = chars * 1000 / duration_ms if duration_ms > 0 else None
            if rate is not None and rate > 60:
                errors.append("unreadable_speed")
            elif rate is not None and rate > 25:
                warnings.append({"id": cue_id, "reason": "fast_reading_speed", "characters_per_second": round(rate, 2)})
            if chars > 160 or len(text.splitlines()) > 4:
                errors.append("oversized_cue")
        segment.diagnostics.pop("subtitle_errors", None)
        if errors:
            segment.diagnostics["subtitle_errors"] = errors
            gaps.append({
                "id": cue_id, "start": segment.start if times_valid else None,
                "end": segment.end if times_valid else None, "text": segment.text,
                "reasons": errors,
            })
        else:
            accepted.append(segment)
            covered_chars += sum(not char.isspace() for char in text)
            previous_end = end_ms
    if transcript.text[cursor:].strip():
        gaps.append({"id": None, "start": None, "end": None, "text": transcript.text[cursor:],
                     "reasons": ["missing_segment_text"]})
    total_chars = sum(not char.isspace() for char in transcript.text)
    transcript.metadata["subtitles"] = {
        "status": ("partial" if accepted else "unavailable") if gaps else ("available" if accepted else "empty"),
        "accepted": len(accepted), "rejected": len(transcript.segments) - len(accepted),
        "covered_characters": covered_chars, "total_characters": total_chars,
        "coverage": min(1.0, covered_chars / total_chars) if total_chars else 1.0,
        "gaps": gaps, "warnings": warnings,
    }
    return accepted


def _segment_text(text: str, speaker: str | None) -> str:
    # Blank lines terminate cues, and markup/arrow sequences change WebVTT
    # parsing. Encode literal text while preserving intentional line wrapping.
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    label = " ".join(speaker.split()) if speaker else None
    return escape(f"[{label}] {text}" if label else text, quote=False)


def render_srt(transcript: Transcript) -> str:
    blocks = []
    for idx, segment in enumerate(validate_subtitles(transcript), 1):
        blocks.append(
            f"{idx}\n{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}\n"
            f"{_segment_text(segment.text, segment.speaker)}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(transcript: Transcript) -> str:
    blocks = ["WEBVTT"]
    for segment in validate_subtitles(transcript):
        blocks.append(
            f"{format_timestamp(segment.start, vtt=True)} --> {format_timestamp(segment.end, vtt=True)}\n"
            f"{_segment_text(segment.text, segment.speaker)}"
        )
    return "\n\n".join(blocks) + "\n"
