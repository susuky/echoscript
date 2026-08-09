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
