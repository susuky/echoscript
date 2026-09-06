from __future__ import annotations

from bisect import bisect_right
from difflib import SequenceMatcher
import re

from echoscript.schema import Transcript


def map_boundaries(source: str, target: str, boundaries: list[int]) -> list[int]:
    """Project text boundaries without translating each token independently.

    Phrase replacements can change length. Interior boundaries are proportional
    character offsets, not newly inferred acoustic timestamps.
    """
    if source == target:
        return boundaries[:]
    # OpenCC leaves punctuation/whitespace unchanged. Anchor at those boundaries
    # to avoid quadratic matching on a long recording with repeated sentences.
    source_parts = re.split(r"(\s+|[，。！？、,.;:!?])", source)
    target_parts = re.split(r"(\s+|[，。！？、,.;:!?])", target)
    if source_parts[1::2] != target_parts[1::2]:
        source_parts, target_parts = [source], [target]
    opcodes = []
    source_offset = target_offset = 0
    for source_part, target_part in zip(source_parts, target_parts):
        # An unusually long punctuation-free span uses difflib's bounded common
        # character heuristic; acoustic timing still belongs to the raw tokens.
        matcher = SequenceMatcher(None, source_part, target_part, autojunk=len(source_part) > 4000)
        opcodes.extend((tag, a + source_offset, b + source_offset, c + target_offset, d + target_offset)
                       for tag, a, b, c, d in matcher.get_opcodes())
        source_offset += len(source_part)
        target_offset += len(target_part)
    starts = [item[1] for item in opcodes]
    mapped = []
    for boundary in boundaries:
        if boundary <= 0:
            mapped.append(0)
        elif boundary >= len(source):
            mapped.append(len(target))
        else:
            _, left, right, out_left, out_right = opcodes[bisect_right(starts, boundary) - 1]
            mapped.append(out_left + (boundary - left) * (out_right - out_left) // max(1, right - left))
    return mapped


def project_text_parts(text: str, parts: list[str]) -> list[str]:
    """Partition one authoritative text across existing token or segment parts."""
    if not parts:
        return []
    boundaries = [0]
    for part in parts:
        boundaries.append(boundaries[-1] + len(part))
    mapped = map_boundaries("".join(parts), text, boundaries)
    return [text[start:end] for start, end in zip(mapped, mapped[1:])]


def segment_text_spans(transcript: Transcript) -> list[tuple[int, int]] | None:
    """Locate segment content in the authoritative display text, in order."""
    spans = []
    cursor = 0
    for segment in transcript.segments:
        part = segment.text.strip()
        start = transcript.text.find(part, cursor)
        if start < 0:
            return None
        spans.append((start, start + len(part)))
        cursor = start + len(part)
    return spans


def normalize_transcript(transcript: Transcript, zh_script: str | None) -> Transcript:
    source = transcript.text
    if transcript.raw_text is None:
        transcript.raw_text = source
    for segment in transcript.segments:
        if segment.raw_text is None:
            segment.raw_text = segment.text
    spans = segment_text_spans(transcript)
    converted = source
    if zh_script in {"tw", "twp"}:
        # Without per-span language labels, converting mixed Japanese text would
        # also alter Japanese kanji (学校 -> 學校).
        if _contains_japanese(transcript.language, source):
            transcript.metadata["normalization"] = {
                "status": "skipped", "reason": "japanese_text_preserved",
            }
        elif _looks_chinese(transcript.language, source):
            try:
                from opencc import OpenCC
            except ImportError as exc:  # pragma: no cover - optional runtime dependency
                raise RuntimeError("Traditional Chinese normalization requires opencc-python-reimplemented") from exc
            # This is the only conversion: every display layer takes slices from it.
            converted = OpenCC("s2twp" if zh_script == "twp" else "s2tw").convert(source)
            transcript.metadata["normalization"] = {"status": "applied", "script": zh_script}

    transcript.text = converted
    if spans is None:
        # An inconsistent imported transcript must not claim its word timings
        # describe the canonical text. Preserve the full text for review/export.
        transcript.metadata["text_consistency"] = {"status": "unavailable", "reason": "segment_text_mismatch"}
        return transcript
    boundaries = [value for span in spans for value in span]
    projected = map_boundaries(source, converted, boundaries)
    separators = []
    cursor = 0
    for segment, start, end in zip(transcript.segments, projected[::2], projected[1::2]):
        separators.append(converted[cursor:start])
        segment.text = converted[start:end]
        if segment.words:
            parts = project_text_parts(segment.text, [word.text for word in segment.words])
            for word, part in zip(segment.words, parts):
                word.text = part
        cursor = end
    separators.append(converted[cursor:])
    transcript.metadata["text_separators"] = separators
    return transcript


def _contains_japanese(language: str | None, text: str) -> bool:
    languages = (language or "").lower().replace("_", "-").split(",")
    return any(lang.strip().split("-")[0] in {"ja", "japanese"} for lang in languages) or any(
        "\u3040" <= char <= "\u30ff" or "\uff66" <= char <= "\uff9d" for char in text
    )


def _looks_chinese(language: str | None, text: str) -> bool:
    lang = (language or "").lower()
    if any(token in lang for token in ("zh", "chinese", "mandarin", "cantonese")):
        return True
    if not text:
        return False
    cjk = sum(1 for ch in text[:1000] if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(2, len(text[:1000]) // 20)
