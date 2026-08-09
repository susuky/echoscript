from __future__ import annotations

from echoscript.schema import SpeakerTurn, Transcript, TranscriptWord, words_to_segments


def assign_speakers(transcript: Transcript, turns: list[SpeakerTurn]) -> Transcript:
    """Assign each timestamped ASR token to the diarization turn with maximum overlap.

    With pyannote Community-1 we prefer exclusive diarization, so midpoint fallback is
    usually enough for tokens that do not overlap due to timestamp jitter.
    """
    if not turns:
        return transcript

    if not transcript.segments:
        transcript.metadata["speaker_attribution"] = {
            "status": "unavailable",
            "reason": "no_timestamped_segments",
        }
        return transcript

    fused_segments = []
    used_segment_fallback = False
    for segment in transcript.segments:
        if segment.words:
            for word in segment.words:
                word.speaker = _speaker_for_word(word, turns)
            fused_segments.extend(words_to_segments(segment.words))
            continue

        # Some ASR backends can return segment timestamps without word timestamps.
        # Preserve those segments and use their interval for best-effort attribution.
        segment.speaker = _speaker_for_interval(segment.start, segment.end, turns)
        fused_segments.append(segment)
        used_segment_fallback = True

    transcript.segments = fused_segments
    transcript.speakers = sorted(
        {
            speaker
            for segment in fused_segments
            for speaker in [segment.speaker, *(word.speaker for word in segment.words)]
            if speaker
        }
    )
    if used_segment_fallback:
        transcript.metadata["speaker_attribution"] = {
            "status": "partial",
            "reason": "segment_fallback_without_word_timestamps",
        }
    return transcript


def _speaker_for_interval(start: float, end: float, turns: list[SpeakerTurn]) -> str | None:
    return _speaker_for_word(TranscriptWord(start=start, end=end, text=""), turns)


def _speaker_for_word(word: TranscriptWord, turns: list[SpeakerTurn]) -> str | None:
    best_speaker: str | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = turn.overlaps(word.start, word.end)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker
    if best_speaker is not None:
        return best_speaker

    midpoint = word.midpoint
    for turn in turns:
        if turn.start <= midpoint <= turn.end:
            return turn.speaker

    nearest = min(turns, key=lambda t: min(abs(midpoint - t.start), abs(midpoint - t.end)))
    distance = min(abs(midpoint - nearest.start), abs(midpoint - nearest.end))
    return nearest.speaker if distance <= 0.75 else None

