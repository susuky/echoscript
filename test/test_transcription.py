from __future__ import annotations

import gc
import sys
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from echoscript.config import Settings
from echoscript.pipeline.fusion import assign_speakers
from echoscript.pipeline.normalize import normalize_transcript
from echoscript.pipeline.pipeline import TranscriptionPipeline
from echoscript.render import render_transcript
from echoscript.schema import JobOptions, SpeakerTurn, Transcript, TranscriptSegment
from echoscript.transcription.faster_whisper import FasterWhisperTranscriber
from echoscript.transcription.qwen import QwenTranscriber, _LANGUAGE_MAP, _normalize_language
from echoscript.worker.model_manager import ModelManager


def _qwen(text, tokens, language="Japanese", *, spans=None):
    transcriber = QwenTranscriber.__new__(QwenTranscriber)
    transcriber.model_name = "test-model"
    transcriber.timestamps_enabled = True
    transcriber.model = Mock()
    transcriber.model.transcribe.return_value = [SimpleNamespace(
        text=text,
        language=language,
        time_stamps=[SimpleNamespace(
            text=token,
            start_time=spans[index][0] if spans else index * 0.1,
            end_time=spans[index][1] if spans else (index + 1) * 0.1,
        ) for index, token in enumerate(tokens)],
    )]
    calls = iter(range(100))
    transcriber.model.transcribe.side_effect = lambda **kwargs: [
        transcriber.model.transcribe.return_value[next(calls)]
    ]
    transcriber.model.forced_aligner.align.side_effect = lambda **kwargs: [
        next(item for item in transcriber.model.transcribe.return_value if item.text == kwargs["text"]).time_stamps
    ]
    return transcriber


@pytest.mark.parametrize(("text", "tokens", "language"), [
    ("The price is $1.50, not $15.00.", ["The", "price", "is", "150", "not", "1500"], "English"),
    ("「学校」で山田先生が説明します。", ["学", "校", "で", "山", "田", "先", "生", "が", "説", "明", "し", "ます"], "Japanese"),
    ("今日はEchoScriptを使います。今天使用 EchoScript，版本 v1.50。", ["今日", "はEchoScript", "を", "使", "い", "ます", "今", "天", "使", "用", "EchoScript", "版本", "v150"], "Japanese,Chinese"),
    ("Cafe\u0301 costs €１.５０ — 𠮷田先生。", ["Cafe", "costs", "１５０", "𠮷", "田", "先", "生"], "Japanese,English"),
])
def test_qwen_preserves_original_text_through_alignment_and_speakers(text, tokens, language):
    transcript = _qwen(text, tokens, language).transcribe("unused.wav", duration=20)
    assert transcript.metadata["alignment"]["status"] == "available"
    assert "".join(word.text for segment in transcript.segments for word in segment.words) == text
    assert " ".join(segment.text for segment in transcript.segments) == text
    fused = assign_speakers(transcript, [SpeakerTurn(0, 20, "SPEAKER_00")])
    assert render_transcript(fused, "txt") == f"[Speaker 1] {text}\n"
    assert text in render_transcript(fused, "srt")


def test_qwen_diarized_text_preserves_spacing_across_subtitle_boundaries():
    text = "日本語の学校について説明します。" * 8
    tokens = [char for char in text if char != "。"]
    transcript = _qwen(text, tokens).transcribe("unused.wav", duration=30)
    assert len(transcript.segments) > 1
    fused = assign_speakers(transcript, [SpeakerTurn(0, 30, "SPEAKER_00")])
    assert render_transcript(fused, "txt") == f"[Speaker 1] {text}\n"


@pytest.mark.parametrize(("tokens", "spans", "reason"), [
    (["Wrong", "text"], None, "text_mismatch"),
    (["Hello"], [(0, float("nan"))], "invalid_timestamps"),
    (["Hello"], [(2, 1)], "invalid_timestamps"),
    (["Hello"], [(0, 21)], "invalid_timestamps"),
    (["Hello"], [(0, 0)], "invalid_timestamps"),
    (["Hello"], [(0, None)], "invalid_timestamps"),
    ([], None, "text_mismatch"),
])
def test_qwen_unreliable_alignment_keeps_text_without_invented_time_or_speaker(tokens, spans, reason):
    transcript = _qwen("Hello.", tokens, "English", spans=spans).transcribe("unused.wav", duration=20)
    assert transcript.text == "Hello."
    assert len(transcript.segments) == 1
    assert transcript.segments[0].text == "Hello."
    assert transcript.segments[0].alignment == "unavailable"
    assert transcript.segments[0].words == []
    assert transcript.metadata["timestamps"] is False
    assert transcript.metadata["alignment"]["status"] == "unavailable"
    assert transcript.metadata["alignment"]["reason"] == reason
    assert transcript.metadata["alignment"]["gaps"][0]["end"] == 20
    fused = assign_speakers(transcript, [SpeakerTurn(0, 20, "SPEAKER_00")])
    assert fused.speakers == []
    assert fused.metadata["speaker_attribution"]["status"] == "unavailable"


def test_all_qwen_languages_and_regional_codes_are_normalized():
    assert len(set(_LANGUAGE_MAP.values())) == 30
    for code, name in _LANGUAGE_MAP.items():
        assert _normalize_language(f" {code.upper()} ") == name
        assert _normalize_language(name) == name
    assert _normalize_language("ja_JP") == "Japanese"
    assert _normalize_language("zh-Hant-TW") == "Chinese"
    assert _normalize_language("auto") is None
    with pytest.raises(ValueError, match="Unsupported Qwen language"):
        _normalize_language("zz")


@pytest.mark.parametrize("requested_language", ["th", None])
def test_qwen_untrained_alignment_language_returns_text_only(requested_language):
    transcriber = _qwen("สวัสดี", ["สวสด"], "Thai")
    transcript = transcriber.transcribe("unused.wav", language=requested_language, duration=3)
    if requested_language:
        assert transcriber.model.transcribe.call_args.kwargs["return_time_stamps"] is False
    assert transcript.text == "สวัสดี"
    assert transcript.segments[0].alignment == "unavailable"
    assert transcript.segments[0].text == "สวัสดี"
    assert transcript.metadata["alignment"]["reason"] == "unsupported_language"


def test_qwen_mixed_language_does_not_force_a_language_and_keeps_context():
    transcriber = _qwen("学校。學校。", ["学校", "學校"], "Japanese,Chinese")
    transcriber.transcribe("unused.wav", language=None, context="山田先生\n運動学 / 動作學", duration=3)
    kwargs = transcriber.model.transcribe.call_args.kwargs
    assert kwargs["language"] is None
    assert kwargs["context"] == "山田先生\n運動学 / 動作學"


def test_untimed_qwen_long_audio_uses_bounded_low_energy_chunks(monkeypatch):
    transcriber = _qwen("前半。", [], "Japanese")
    transcriber.model.transcribe.return_value.append(SimpleNamespace(text="後半。", language="Japanese"))
    utils = SimpleNamespace(
        SAMPLE_RATE=16000,
        normalize_audios=Mock(return_value=["waveform"]),
        split_audio_into_chunks=Mock(return_value=[("first", 0), ("second", 60)]),
    )
    monkeypatch.setitem(sys.modules, "qwen_asr.inference.utils", utils)
    result = transcriber.transcribe("long.wav", timestamps=False, duration=120)
    utils.split_audio_into_chunks.assert_called_once_with("waveform", 16000, 60)
    assert [call.kwargs["audio"] for call in transcriber.model.transcribe.call_args_list] == [
        ("first", 16000), ("second", 16000),
    ]
    assert result.text == "前半。\n後半。"
    assert [segment.text for segment in result.segments] == ["前半。", "後半。"]
    assert all(segment.alignment == "disabled" for segment in result.segments)


def test_timed_long_audio_preserves_every_chunk_and_absolute_timestamps(monkeypatch):
    transcriber = _qwen("前半。", ["前半"])
    transcriber.model.transcribe.return_value.append(SimpleNamespace(
        text="後半。", language="Japanese",
        time_stamps=[SimpleNamespace(text="後半", start_time=2, end_time=3)],
    ))
    monkeypatch.setitem(sys.modules, "qwen_asr.inference.utils", SimpleNamespace(
        SAMPLE_RATE=16000,
        normalize_audios=Mock(return_value=["waveform"]),
        split_audio_into_chunks=Mock(return_value=[("first", 0), ("second", 59.5)]),
    ))
    result = transcriber.transcribe("long.wav", timestamps=True, duration=100)
    words = [word for segment in result.segments for word in segment.words]
    assert result.text == "前半。\n後半。" == "".join(word.text for word in words)
    assert (words[-1].start, words[-1].end) == (61.5, 62.5)
    assert result.metadata["alignment"]["status"] == "available"


def test_missing_later_chunk_timestamps_never_drop_its_text(monkeypatch):
    transcriber = _qwen("前半。", ["前半"])
    transcriber.model.transcribe.return_value.append(SimpleNamespace(
        text="後半。", language="Japanese", time_stamps=None,
    ))
    monkeypatch.setitem(sys.modules, "qwen_asr.inference.utils", SimpleNamespace(
        SAMPLE_RATE=16000,
        normalize_audios=Mock(return_value=["waveform"]),
        split_audio_into_chunks=Mock(return_value=[("first", 0), ("second", 60)]),
    ))
    result = transcriber.transcribe("long.wav", duration=100)
    assert result.text == "前半。\n後半。"
    assert result.segments[0].alignment == "available"
    assert result.segments[0].words
    assert result.segments[-1].text == "後半。"
    assert result.segments[-1].alignment == "unavailable"
    assert result.metadata["alignment"]["status"] == "partial"
    assert result.metadata["alignment"]["gaps"] == [
        {"segment_id": "segment-0001", "start": 60, "end": 100, "reason": "missing_timestamps"},
    ]


@pytest.mark.parametrize(("language", "text"), [
    ("Japanese", "学校の説明です。"),
    ("ja-JP", "学校"),
    ("Japanese,Chinese", "学校。這是學校。"),
    ("Chinese", "学校で説明します。学校的说明。"),
])
def test_traditional_chinese_normalization_preserves_japanese(language, text):
    transcript = Transcript(text=text, language=language, segments=[TranscriptSegment(0, 1, text)])
    normalized = normalize_transcript(transcript, "tw")
    assert normalized.text == text
    assert normalized.segments[0].text == text
    assert normalized.metadata["normalization"]["reason"] == "japanese_text_preserved"


def test_traditional_chinese_still_converts_chinese_and_english():
    transcript = Transcript(text="这是学校的 EchoScript 项目。", language="Chinese,English")
    assert normalize_transcript(transcript, "tw").text == "這是學校的 EchoScript 項目。"


def test_faster_whisper_separates_context_glossary_and_keeps_previous_text_configurable():
    transcriber = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    transcriber.model_name, transcriber.compute_type = "test", "float16"
    transcriber.model = Mock()
    transcriber.model.transcribe.return_value = ([], SimpleNamespace(language="ja", duration=5))
    transcriber.transcribe("unused.wav", language="ja_JP", context=" 山田先生、運動学 ", glossary="EchoScript")
    kwargs = transcriber.model.transcribe.call_args.kwargs
    assert kwargs["language"] == "ja"
    assert kwargs["initial_prompt"] == "山田先生、運動学"
    assert kwargs["hotwords"] == "EchoScript"
    assert kwargs["multilingual"] is False
    assert kwargs["condition_on_previous_text"] is True
    assert kwargs["vad_filter"] is True
    assert kwargs["beam_size"] == 5
    transcriber.transcribe("unused.wav", context="context", previous_text="previous",
                           condition_on_previous_text=False)
    kwargs = transcriber.model.transcribe.call_args.kwargs
    assert kwargs["initial_prompt"] == "context"
    assert kwargs["hotwords"] is None
    assert kwargs["condition_on_previous_text"] is False


def test_pipeline_releases_asr_before_diarization_and_omits_unreliable_subtitles(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "jobs.sqlite", jobs_dir=tmp_path / "jobs")
    models = ModelManager()
    options = JobOptions(diarize=True, zh_script=None)

    class ASR:
        def transcribe(self, *args, **kwargs):
            return Transcript(text="Untimed transcript.", metadata={"timestamps": False})

    models._asr = ASR()
    model_ref = weakref.ref(models._asr)
    models._asr_key = models._desired_asr_key(options)

    def get_diarizer(_options):
        gc.collect()
        assert model_ref() is None
        return SimpleNamespace(diarize=lambda *args, **kwargs: [SpeakerTurn(0, 1, "A")])

    monkeypatch.setattr(models, "get_diarizer", get_diarizer)
    monkeypatch.setattr("echoscript.worker.model_manager._best_effort_cuda_cleanup", gc.collect)
    monkeypatch.setattr("echoscript.pipeline.pipeline.probe_duration", lambda *args, **kwargs: 1)
    audio = settings.jobs_dir / "check" / "audio.wav"
    audio.parent.mkdir(parents=True)
    import wave
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 16000)
    output = TranscriptionPipeline(settings, models).run({
        "id": "check", "options": options.to_dict(), "source_type": "upload", "media_path": str(audio),
    })
    assert output.exists()
    assert output.with_name("result.txt").read_text() == "Untimed transcript.\n"
    assert not output.with_name("result.srt").exists()
    assert not output.with_name("result.vtt").exists()


def test_retry_reuses_completed_download_with_new_name(tmp_path):
    from unittest.mock import patch
    from echoscript.config import Settings
    from echoscript.pipeline.pipeline import TranscriptionPipeline
    directory = tmp_path/'jobs'/'retry'
    directory.mkdir(parents=True)
    complete = directory/'source-Youtube-video-0123456789ab.webm'
    complete.write_bytes(b'complete download')
    (directory/'source-Youtube-video-0123456789ab.webm.part').write_bytes(b'partial')
    pipeline = TranscriptionPipeline(Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.sqlite3', jobs_dir=tmp_path/'jobs'), None)
    with patch('echoscript.pipeline.pipeline.download_public_url') as download:
        path = pipeline._resolve_media({'source_type': 'url', 'source_value': 'https://example.com/media'}, directory)
    assert path == complete
    download.assert_not_called()


def test_retry_never_uses_partial_download_as_completed_media(tmp_path):
    from unittest.mock import patch
    from echoscript.config import Settings
    from echoscript.pipeline.pipeline import TranscriptionPipeline
    (tmp_path/'source.webm.part').write_bytes(b'partial')
    (tmp_path/'source-Youtube-id-hash.webm.part').write_bytes(b'partial')
    pipeline = TranscriptionPipeline(Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.sqlite3', jobs_dir=tmp_path/'jobs'), None)
    with patch('echoscript.pipeline.pipeline.download_public_url', return_value=tmp_path/'finished.wav') as download:
        assert pipeline._resolve_media({'source_type': 'url', 'source_value': 'https://example.com/media'}, tmp_path) == tmp_path/'finished.wav'
    download.assert_called_once()


def test_qwen_alignment_exception_keeps_saved_asr_and_retry_does_not_decode_again():
    transcriber = _qwen("Hello.", ["Hello"], "English")
    saved = transcriber.transcribe("unused.wav", duration=2, timestamps=False)
    transcriber.model.forced_aligner.align.side_effect = RuntimeError("temporary aligner failure")
    failed = transcriber.align("unused.wav", saved, duration=2)
    assert saved.segments[0].alignment == "disabled"
    assert failed.segments[0].text == "Hello."
    assert failed.segments[0].diagnostics["alignment_error"] == "RuntimeError"
    assert failed.metadata["alignment"]["reason"] == "alignment_failed"
    transcriber.model.forced_aligner.align.side_effect = None
    transcriber.model.forced_aligner.align.return_value = [
        [SimpleNamespace(text="Hello", start_time=0.1, end_time=0.8)]
    ]
    restored = transcriber.align("unused.wav", failed, duration=2)
    assert restored.metadata["alignment"]["status"] == "available"
    assert restored.segments[0].text == "Hello."
    transcriber.model.transcribe.assert_called_once()
    assert transcriber.model.transcribe.call_args.kwargs["return_time_stamps"] is False
    transcriber.align("unused.wav", restored, duration=2)
    assert transcriber.model.forced_aligner.align.call_count == 2


def test_qwen_alignment_gap_keeps_reliable_segments_on_both_sides(monkeypatch):
    transcriber = _qwen("First.", ["First"], "English")
    transcriber.model.transcribe.return_value.extend([
        SimpleNamespace(text="Middle.", language="English", time_stamps=None),
        SimpleNamespace(text="Last.", language="English", time_stamps=[
            SimpleNamespace(text="Last", start_time=0.2, end_time=0.9),
        ]),
    ])
    monkeypatch.setitem(sys.modules, "qwen_asr.inference.utils", SimpleNamespace(
        SAMPLE_RATE=16000, normalize_audios=Mock(return_value=["waveform"]),
        split_audio_into_chunks=Mock(return_value=[("a", 0), ("b", 60), ("c", 120)]),
    ))
    transcript = transcriber.transcribe("long.wav", duration=150)
    assert transcript.text == "First.\nMiddle.\nLast."
    assert [segment.alignment for segment in transcript.segments] == ["available", "unavailable", "available"]
    assert transcript.segments[-1].words[0].start == 120.2
    assert transcript.metadata["alignment"]["status"] == "partial"


def test_qwen_optional_generation_diagnostics_are_retained():
    transcriber = _qwen("Cut short", [], "English")
    result = transcriber.model.transcribe.return_value[0]
    result.finish_reason = "length"
    result.generated_tokens = 4096
    result.raw_output = "language English<asr_text>Cut short"
    transcript = transcriber.transcribe("unused.wav", duration=20, timestamps=False)
    diagnostics = transcript.segments[0].diagnostics
    assert diagnostics["finish_reason"] == "length"
    assert diagnostics["generated_tokens"] == 4096
    assert diagnostics["raw_output"].endswith("Cut short")
    assert diagnostics["generation_diagnostics_available"] is True


@pytest.mark.parametrize("backend", ["qwen", "faster-whisper"])
def test_backend_prompt_budgets_preserve_unicode_and_report_truncation(backend):
    from echoscript.transcription.prompts import prepare_prompts

    class ByteTokenizer:
        def encode(self, text, add_special_tokens=False):
            return list(text.encode("utf-8"))

    prompt, hotwords, diagnostics = prepare_prompts(
        backend, context="日本語の文脈。" * 300, glossary="EchoScript, Qwen, Whisper, 山田先生" * 50,
        previous_text="前文。" * 200 + "結尾", tokenizer=ByteTokenizer(),
    )
    assert "\ufffd" not in prompt + hotwords
    assert diagnostics["context"]["truncated"] is True
    assert diagnostics["glossary"]["truncated"] is True
    assert diagnostics["previous_text"]["truncated"] is True
    assert diagnostics["context"]["counter"] == "model_tokenizer"
    assert prompt.endswith("結尾")
    assert diagnostics["combined"]["used_tokens"] + (
        diagnostics["glossary"]["used_tokens"] if backend == "faster-whisper" else 0
    ) <= diagnostics["input_budget"]
    assert bool(hotwords) is (backend == "faster-whisper")


def test_whisper_keeps_model_quality_diagnostics_and_word_confidence():
    transcriber = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    transcriber.model_name, transcriber.compute_type = "test", "float16"
    transcriber.model = Mock()
    transcriber.model.transcribe.return_value = ([SimpleNamespace(
        id=0, seek=0, start=0, end=2, text="Hello.", tokens=[1, 2],
        avg_logprob=-0.6, compression_ratio=1.2, no_speech_prob=0.3, temperature=0.2,
        words=[SimpleNamespace(start=0.1, end=1, word="Hello.", probability=0.8)],
    )], SimpleNamespace(language="en", duration=3, language_probability=0.9, duration_after_vad=2))
    transcript = transcriber.transcribe("unused.wav")
    assert transcript.segments[0].diagnostics["tokens"] == [1, 2]
    assert transcript.segments[0].diagnostics["no_speech_prob"] == 0.3
    assert transcript.segments[0].words[0].confidence == 0.8
    assert transcript.metadata["model_diagnostics"]["duration_after_vad"] == 2


def test_qwen_aligner_load_failure_happens_after_asr_and_can_be_retried(monkeypatch):
    model = Mock(forced_aligner=None)
    model.transcribe.return_value = [SimpleNamespace(text="Hello.", language="English")]
    asr_factory = Mock(return_value=model)
    aligner_factory = Mock(side_effect=RuntimeError("aligner weights unavailable"))
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(
        Qwen3ASRModel=SimpleNamespace(from_pretrained=asr_factory),
        Qwen3ForcedAligner=SimpleNamespace(from_pretrained=aligner_factory),
    ))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(float32="float32"))
    transcriber = QwenTranscriber("test", device="cpu")
    assert "forced_aligner" not in asr_factory.call_args.kwargs
    assert transcriber.model_key == "qwen:test+aligner"
    aligner_factory.assert_not_called()
    saved = transcriber.transcribe("unused.wav", duration=2, timestamps=False)
    assert saved.text == "Hello."
    aligner_factory.assert_not_called()
    failed = transcriber.align("unused.wav", saved, duration=2)
    assert failed.text == "Hello."
    assert failed.segments[0].diagnostics["alignment_error_message"] == "aligner weights unavailable"
    assert failed.metadata["alignment"]["status"] == "unavailable"
    aligner_factory.side_effect = None
    aligner_factory.return_value = SimpleNamespace(align=lambda **kwargs: [[
        SimpleNamespace(text="Hello", start_time=0, end_time=1),
    ]])
    restored = transcriber.align("unused.wav", failed, duration=2)
    assert restored.metadata["alignment"]["status"] == "available"
    assert "alignment_error" not in restored.segments[0].diagnostics
    model.transcribe.assert_called_once()
