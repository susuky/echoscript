from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord


def test_transcript_roundtrip():
    original = Transcript(
        text="abc",
        language="en",
        segments=[TranscriptSegment(0, 1, "abc", words=[TranscriptWord(0, 1, "abc")])],
    )
    restored = Transcript.from_dict(original.to_dict())
    assert restored == original


def test_legacy_transcript_and_segment_checkpoint_fields_roundtrip():
    legacy = Transcript.from_dict({"text": "原文", "segments": [{"start": 0, "end": 1, "text": "原文"}]})
    assert legacy.segments[0].alignment == "available"
    legacy.raw_text = "原文"
    legacy.segments[0].id = "chunk-001"
    legacy.segments[0].raw_text = "原文"
    legacy.segments[0].diagnostics = {"alignment_reason": "missing_timestamps"}
    legacy.segments[0].alignment = "unavailable"
    assert Transcript.from_dict(legacy.to_dict()) == legacy


def test_edit_rebuild_preserves_raw_source_and_exact_separators():
    from echoscript.pipeline.normalize import normalize_transcript
    from echoscript.schema import rebuild_transcript_text

    transcript = Transcript(text="Hello.\n学校です。", segments=[
        TranscriptSegment(0, 1, "Hello."), TranscriptSegment(1, 2, "学校です。"),
    ])
    normalize_transcript(transcript, None)
    transcript.segments[0].text = "Hi!"
    rebuild_transcript_text(transcript)
    assert transcript.text == "Hi!\n学校です。"
    assert transcript.raw_text == "Hello.\n学校です。"
    assert transcript.segments[0].raw_text == "Hello."
