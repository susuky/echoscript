from types import SimpleNamespace
from unittest.mock import Mock
import sys

from echoscript.pipeline.normalize import normalize_transcript
from echoscript.render import render_transcript
from echoscript.schema import Transcript, TranscriptSegment, TranscriptWord, rebuild_transcript_text


def test_phrase_conversion_runs_once_and_all_views_slice_the_same_result(monkeypatch):
    converter = Mock()
    converter.convert.return_value = "使用區域網路和軟體。"
    monkeypatch.setitem(sys.modules, "opencc", SimpleNamespace(OpenCC=Mock(return_value=converter)))
    transcript = Transcript(text="使用局域网和软件。", language="zh", segments=[
        TranscriptSegment(0, 2, "使用局域", speaker="A", words=[TranscriptWord(0, 1, "使用"), TranscriptWord(1, 2, "局域")]),
        TranscriptSegment(2, 4, "网和软件。", speaker="A", words=[TranscriptWord(2, 3, "网"), TranscriptWord(3, 4, "和软件。")]),
    ])
    normalize_transcript(transcript, "twp")
    converter.convert.assert_called_once_with("使用局域网和软件。")
    assert transcript.text == "使用區域網路和軟體。"
    assert "".join(segment.text for segment in transcript.segments) == transcript.text
    assert "".join(word.text for segment in transcript.segments for word in segment.words) == transcript.text
    assert render_transcript(transcript, "txt") == "[A] 使用區域網路和軟體。\n"
    assert transcript.raw_text == "使用局域网和软件。"
    assert [segment.raw_text for segment in transcript.segments] == ["使用局域", "网和软件。"]
    transcript.segments[1].text = "網路與軟體。"
    rebuild_transcript_text(transcript)
    assert "網路與軟體。" in render_transcript(transcript, "txt")
    assert "網路與軟體。" in render_transcript(transcript, "srt")
    assert "網路與軟體。" in render_transcript(transcript, "json")
    assert transcript.raw_text == "使用局域网和软件。"


def test_mixed_japanese_text_preserved_and_raw_source_captured():
    text = "学校で説明します。接着介绍局域网。"
    transcript = Transcript(text=text, language="Japanese,Chinese", segments=[TranscriptSegment(0, 5, text)])
    normalize_transcript(transcript, "twp")
    assert transcript.text == transcript.raw_text == text
    assert transcript.segments[0].raw_text == text
    assert transcript.metadata["normalization"]["reason"] == "japanese_text_preserved"
