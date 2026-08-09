from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord


def test_transcript_roundtrip():
    original = Transcript(
        text="abc",
        language="en",
        segments=[TranscriptSegment(0, 1, "abc", words=[TranscriptWord(0, 1, "abc")])],
    )
    restored = Transcript.from_dict(original.to_dict())
    assert restored == original
