from __future__ import annotations

from echoscript.schema import Transcript


def normalize_transcript(transcript: Transcript, zh_script: str | None) -> Transcript:
    if zh_script not in {"tw", "twp"}:
        return transcript
    if not _looks_chinese(transcript.language, transcript.text):
        return transcript

    try:
        from opencc import OpenCC
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise RuntimeError("Traditional Chinese normalization requires opencc-python-reimplemented") from exc

    converter = OpenCC("s2twp" if zh_script == "twp" else "s2tw")
    transcript.text = converter.convert(transcript.text)
    for segment in transcript.segments:
        segment.text = converter.convert(segment.text)
        for word in segment.words:
            word.text = converter.convert(word.text)
    return transcript


def _looks_chinese(language: str | None, text: str) -> bool:
    lang = (language or "").lower()
    if any(token in lang for token in ("zh", "chinese", "mandarin", "cantonese")):
        return True
    if not text:
        return False
    cjk = sum(1 for ch in text[:1000] if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(2, len(text[:1000]) // 20)
