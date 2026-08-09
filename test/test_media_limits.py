from types import SimpleNamespace
import subprocess
from unittest.mock import patch

import pytest

from echoscript.media.ffmpeg import extract_audio
from echoscript.pipeline.pipeline import TranscriptionPipeline


def _settings(tmp_path, max_duration=10):
    return SimpleNamespace(
        jobs_dir=tmp_path / "jobs",
        max_media_duration_seconds=max_duration,
        max_remote_download_bytes=1024,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
        release_between_stages=True,
    )


def _upload_job(media_path):
    return {
        "id": "job",
        "source_type": "upload",
        "source_value": media_path.name,
        "media_path": str(media_path),
        "options": {},
    }


def test_pipeline_rejects_known_oversized_source_before_extract(tmp_path):
    media = tmp_path / "source.mp4"
    media.touch()
    pipeline = TranscriptionPipeline(_settings(tmp_path), models=object())
    with patch("echoscript.pipeline.pipeline.probe_duration", return_value=10.1), patch(
        "echoscript.pipeline.pipeline.extract_audio"
    ) as extract:
        with pytest.raises(ValueError, match="exceeds configured limit"):
            pipeline.run(_upload_job(media))
    extract.assert_not_called()


def test_pipeline_rejects_audio_that_reaches_hard_truncation_boundary(tmp_path):
    media = tmp_path / "source.bin"
    media.touch()
    pipeline = TranscriptionPipeline(_settings(tmp_path), models=object())

    def fake_extract(_source, output, **_kwargs):
        output.touch()
        return output

    with patch(
        "echoscript.pipeline.pipeline.probe_duration", side_effect=[None, 10.0]
    ), patch("echoscript.pipeline.pipeline.extract_audio", side_effect=fake_extract) as extract:
        with pytest.raises(ValueError, match="reached configured duration limit"):
            pipeline.run(_upload_job(media))
    assert extract.call_args.kwargs["max_duration_seconds"] == 10


def test_extract_audio_adds_hard_limit_and_removes_oversized_pcm(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.touch()
    output = tmp_path / "audio.wav"
    captured = {}

    def fake_run(command, check):
        assert check is True
        captured["command"] = command
        output.write_bytes(b"x" * 1_100_000)

    monkeypatch.setattr("echoscript.media.ffmpeg.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="PCM audio exceeds"):
        extract_audio(source, output, sample_rate=16_000, max_duration_seconds=1)
    assert "-t" in captured["command"]
    assert captured["command"][captured["command"].index("-t") + 1] == "1"
    assert not output.exists()


def test_extract_audio_removes_partial_output_when_ffmpeg_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.touch()
    output = tmp_path / "audio.wav"

    def failing_run(command, check):
        assert check is True
        output.write_bytes(b"partial wav")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("echoscript.media.ffmpeg.subprocess.run", failing_run)
    with pytest.raises(subprocess.CalledProcessError):
        extract_audio(source, output, max_duration_seconds=10)
    assert not output.exists()
