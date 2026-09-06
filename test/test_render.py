from echoscript.render import render_srt, render_transcript, render_vtt
from echoscript.render.subtitle import format_timestamp
from echoscript.schema import Transcript, TranscriptSegment


def test_timestamp_rounding():
    assert format_timestamp(0.5) == "00:00:00,500"
    assert format_timestamp(3661.05) == "01:01:01,050"
    assert format_timestamp(5.678, vtt=True) == "00:00:05.678"


def test_srt_with_speaker():
    transcript = Transcript(
        text="hello",
        segments=[TranscriptSegment(1.0, 2.5, "hello", speaker="SPEAKER_00")],
    )
    assert render_srt(transcript) == (
        "1\n00:00:01,000 --> 00:00:02,500\n[SPEAKER_00] hello\n"
    )


def test_vtt_header():
    transcript = Transcript(text="hello", segments=[TranscriptSegment(0, 1, "hello")])
    assert render_vtt(transcript).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.000")


def test_txt_groups_consecutive_diarized_segments_by_speaker():
    transcript = Transcript(
        text="hello there goodbye",
        speakers=["SPEAKER_00", "SPEAKER_01"],
        segments=[
            TranscriptSegment(0, 1, "hello", speaker="SPEAKER_00"),
            TranscriptSegment(1, 2, "there", speaker="SPEAKER_00"),
            TranscriptSegment(2, 3, "goodbye", speaker="SPEAKER_01"),
        ],
    )

    assert render_transcript(transcript, "txt") == (
        "[Speaker 1] hello there\n\n[Speaker 2] goodbye\n"
    )


def test_final_subtitles_keep_reliable_cues_and_report_every_gap():
    from echoscript.render import validate_subtitles

    transcript = Transcript(text="good zero unknown later", duration=5, segments=[
        TranscriptSegment(0, 1, "good", id="a"),
        TranscriptSegment(1, 1.0001, "zero", id="b"),
        TranscriptSegment(1, 3, "unknown", id="c", alignment="unavailable"),
        TranscriptSegment(3, 5, "later", id="d"),
    ])
    assert [segment.id for segment in validate_subtitles(transcript)] == ["a", "d"]
    assert "nonpositive_duration" in transcript.segments[1].diagnostics["subtitle_errors"]
    report = transcript.metadata["subtitles"]
    assert report["status"] == "partial"
    assert report["coverage"] == 9 / 20
    assert [gap["id"] for gap in report["gaps"]] == ["b", "c"]
    srt = render_srt(transcript)
    assert "good" in srt and "later" in srt
    assert "zero" not in srt and "unknown" not in srt
    assert "2\n00:00:03,000" in srt
    assert render_transcript(transcript, "txt") == "good zero unknown later\n"


def test_final_subtitle_gates_reject_invalid_intervals_and_unreadable_text():
    import pytest
    from echoscript.render import validate_subtitles

    for start, end, text, reason in [
        (float("nan"), 1, "x", "nonfinite_timestamps"),
        (-1, 1, "x", "negative_timestamp"),
        (0, 0.01, "x", "too_short"),
        (0, 1, "x" * 61, "unreadable_speed"),
        (0, 6, "x", "outside_audio"),
        (0, 1, "   ", "empty_text"),
    ]:
        transcript = Transcript(text=text, duration=5, segments=[TranscriptSegment(start, end, text)])
        assert validate_subtitles(transcript) == []
        assert reason in transcript.segments[0].diagnostics["subtitle_errors"]
    for invalid in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            format_timestamp(invalid)


def test_diarized_text_uses_canonical_display_even_when_words_are_stale():
    from echoscript.schema import TranscriptWord

    transcript = Transcript(text="修改後 未對齊 下一句", segments=[
        TranscriptSegment(0, 1, "修改後", speaker="A", words=[TranscriptWord(0, 1, "舊文字")]),
        TranscriptSegment(1, 2, "未對齊", alignment="unavailable"),
        TranscriptSegment(2, 3, "下一句", speaker="B"),
    ], metadata={"word_text_verbatim": True})
    assert render_transcript(transcript, "txt") == "[A] 修改後\n\n未對齊\n\n[B] 下一句\n"
    assert "舊文字" not in render_srt(transcript)


def test_missing_text_and_overlapping_cue_remain_explicit():
    from echoscript.render import validate_subtitles

    transcript = Transcript(text="first missing overlap final", segments=[
        TranscriptSegment(0, 2, "first"),
        TranscriptSegment(1, 3, "overlap"),
        TranscriptSegment(3, 4, "final"),
    ])
    assert [item.text for item in validate_subtitles(transcript)] == ["first", "final"]
    assert [gap["reasons"] for gap in transcript.metadata["subtitles"]["gaps"]] == [
        ["missing_segment_text"], ["overlap_or_out_of_order"],
    ]
    transcript.segments[0].speaker = "A"
    assert "missing" in render_transcript(transcript, "txt")


def test_literal_markup_arrows_and_blank_lines_cannot_change_cue_structure():
    transcript = Transcript(text="A <b> & B\n\nC --> D", segments=[
        TranscriptSegment(0, 3, "A <b> & B\n\nC --> D"),
    ])
    srt = render_srt(transcript)
    vtt = render_vtt(transcript)
    assert "A &lt;b&gt; &amp; B\nC --&gt; D" in srt
    assert "A &lt;b&gt; &amp; B\nC --&gt; D" in vtt
    assert len(srt.strip().split("\n\n")) == 1
    assert vtt.count(" --> ") == 1
    assert render_transcript(transcript, "txt") == transcript.text + "\n"


def test_invisible_and_control_only_cues_are_unusable():
    from echoscript.render import validate_subtitles
    for text, reason in [("\u200b\ufeff", "empty_text"), ("text\x00", "unsupported_control_character")]:
        transcript = Transcript(text=text, segments=[TranscriptSegment(0, 1, text)])
        assert validate_subtitles(transcript) == []
        assert reason in transcript.segments[0].diagnostics["subtitle_errors"]
