from echoscript.pipeline.fusion import assign_speakers
from echoscript.schema import SpeakerTurn, Transcript, TranscriptSegment, TranscriptWord, words_to_segments


def test_assign_speaker_and_split_on_change():
    words = [
        TranscriptWord(0.0, 0.5, "Hello"),
        TranscriptWord(0.5, 1.0, " world"),
        TranscriptWord(1.1, 1.5, "Hi"),
    ]
    transcript = Transcript(text="Hello world Hi", segments=[TranscriptSegment(0, 1.5, "", words=words)])
    turns = [SpeakerTurn(0, 1.05, "A"), SpeakerTurn(1.05, 2, "B")]
    fused = assign_speakers(transcript, turns)
    assert [segment.speaker for segment in fused.segments] == ["A", "B"]
    assert fused.speakers == ["A", "B"]


def test_cjk_tokens_join_without_spaces():
    segments = words_to_segments([
        TranscriptWord(0, .2, "你"),
        TranscriptWord(.2, .4, "好"),
        TranscriptWord(.4, .6, "。"),
    ])
    assert segments[0].text == "你好。"


def test_assign_speaker_preserves_segment_without_words():
    segment = TranscriptSegment(0.0, 2.0, "untimed words")
    transcript = Transcript(text="untimed words", segments=[segment])
    fused = assign_speakers(transcript, [SpeakerTurn(0.0, 2.0, "A")])
    assert fused.text == "untimed words"
    assert fused.segments == [segment]
    assert fused.segments[0].speaker == "A"
    assert fused.speakers == ["A"]
    assert fused.metadata["speaker_attribution"]["status"] == "partial"


def test_assign_speaker_preserves_wordless_segments_in_mixed_transcript():
    with_words = TranscriptSegment(
        0.0,
        1.0,
        "hello",
        words=[TranscriptWord(0.0, 1.0, "hello")],
    )
    without_words = TranscriptSegment(1.0, 2.0, "fallback")
    transcript = Transcript(text="hello fallback", segments=[with_words, without_words])
    turns = [SpeakerTurn(0.0, 1.0, "A"), SpeakerTurn(1.0, 2.0, "B")]
    fused = assign_speakers(transcript, turns)
    assert [segment.text for segment in fused.segments] == ["hello", "fallback"]
    assert [segment.speaker for segment in fused.segments] == ["A", "B"]
    assert fused.speakers == ["A", "B"]


def test_empty_diarization_does_not_change_transcript():
    segment = TranscriptSegment(0.0, 1.0, "hello")
    transcript = Transcript(text="hello", segments=[segment], metadata={"source": "test"})
    assert assign_speakers(transcript, []) is transcript
    assert transcript.text == "hello"
    assert transcript.segments == [segment]
    assert transcript.metadata == {"source": "test"}


def test_no_segments_preserves_text_and_reports_unavailable_attribution():
    transcript = Transcript(text="text without timestamps")
    fused = assign_speakers(transcript, [SpeakerTurn(0.0, 1.0, "A")])
    assert fused.text == "text without timestamps"
    assert fused.segments == []
    assert fused.metadata["speaker_attribution"] == {
        "status": "unavailable",
        "reason": "no_timestamped_segments",
    }
