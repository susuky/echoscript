from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass
class TranscriptWord:
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[TranscriptWord] = field(default_factory=list)
    id: str | None = None
    raw_text: str | None = None
    alignment: str = "available"
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str

    def overlaps(self, start: float, end: float) -> float:
        return max(0.0, min(self.end, end) - max(self.start, start))


@dataclass
class Transcript:
    text: str
    language: str | None = None
    duration: float | None = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transcript":
        segments: list[TranscriptSegment] = []
        for raw_segment in data.get("segments", []):
            words = [TranscriptWord(**raw_word) for raw_word in raw_segment.get("words", [])]
            segment_data = {k: v for k, v in raw_segment.items() if k != "words"}
            segments.append(TranscriptSegment(**segment_data, words=words))
        return cls(
            text=data.get("text", ""),
            language=data.get("language"),
            duration=data.get("duration"),
            segments=segments,
            speakers=list(data.get("speakers", [])),
            metadata=dict(data.get("metadata", {})),
            raw_text=data.get("raw_text"),
        )


def rebuild_transcript_text(transcript: Transcript) -> Transcript:
    """Rebuild display text after edits, keeping the original ASR text immutable."""
    if transcript.raw_text is None:
        transcript.raw_text = transcript.text
    for segment in transcript.segments:
        if segment.raw_text is None:
            segment.raw_text = segment.text
    if not transcript.segments:
        return transcript
    separators = transcript.metadata.get("text_separators")
    if isinstance(separators, list) and len(separators) == len(transcript.segments) + 1:
        transcript.text = separators[0] + "".join(
            segment.text + separator
            for segment, separator in zip(transcript.segments, separators[1:])
        )
    else:
        transcript.text = _join_tokens([segment.text for segment in transcript.segments])
    return transcript


def words_to_segments(
    words: Iterable[TranscriptWord],
    *,
    max_duration: float = 6.0,
    max_chars: int = 48,
    max_gap: float = 0.9,
    preserve_spacing: bool = False,
) -> list[TranscriptSegment]:
    """Group timestamped tokens into subtitle-friendly segments.

    A new segment starts on speaker changes, long pauses, long duration, or long text.
    This works for both whitespace-delimited languages and CJK character timestamps.
    """
    output: list[TranscriptSegment] = []
    current: list[TranscriptWord] = []
    join_tokens = (lambda tokens: "".join(tokens).strip()) if preserve_spacing else _join_tokens

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = join_tokens([w.text for w in current])
        output.append(
            TranscriptSegment(
                start=current[0].start,
                end=current[-1].end,
                text=text,
                speaker=current[0].speaker,
                words=list(current),
            )
        )
        current = []

    for word in words:
        if not current:
            current.append(word)
            continue

        candidate_text = join_tokens([*(w.text for w in current), word.text])
        duration = word.end - current[0].start
        gap = word.start - current[-1].end
        speaker_changed = word.speaker != current[0].speaker
        if speaker_changed or gap > max_gap or duration > max_duration or len(candidate_text) > max_chars:
            flush()
        current.append(word)

    flush()
    return output


def _join_tokens(tokens: list[str]) -> str:
    result = ""
    for token in tokens:
        if not token:
            continue
        if not result:
            result = token.strip()
            continue
        # faster-whisper word tokens often contain their own leading whitespace.
        if token[:1].isspace():
            result += token
        elif _is_cjkish(result[-1:]) or _is_cjkish(token[:1]):
            result += token
        elif token in {".", ",", "!", "?", ":", ";", "'", '"', ")", "]", "}"}:
            result += token
        else:
            result += " " + token
    return result.strip()


def _is_cjkish(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF  # Hiragana/Katakana
        or 0x3400 <= code <= 0x9FFF  # CJK
        or 0xF900 <= code <= 0xFAFF
    )
