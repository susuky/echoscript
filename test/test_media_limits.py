from pathlib import Path
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
    output.write_bytes(b"previous complete audio")

    def fake_run(command, check):
        assert check is True
        captured["command"] = command
        Path(command[-1]).write_bytes(b"x" * 1_100_000)

    monkeypatch.setattr("echoscript.media.ffmpeg.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="PCM audio exceeds"):
        extract_audio(source, output, sample_rate=16_000, max_duration_seconds=1)
    assert "-t" in captured["command"]
    assert captured["command"][captured["command"].index("-t") + 1] == "1"
    assert output.read_bytes() == b"previous complete audio"
    assert not list(tmp_path.glob("*.part.wav"))


def test_extract_audio_removes_partial_output_when_ffmpeg_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.touch()
    output = tmp_path / "audio.wav"

    def failing_run(command, check):
        assert check is True
        Path(command[-1]).write_bytes(b"partial wav")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("echoscript.media.ffmpeg.subprocess.run", failing_run)
    with pytest.raises(subprocess.CalledProcessError):
        extract_audio(source, output, max_duration_seconds=10)
    assert not output.exists()
    assert not list(tmp_path.glob("*.part.wav"))


def test_ffmpeg_accepts_real_media_but_rejects_local_playlist(tmp_path):
    import shutil
    import wave
    from echoscript.media.ffmpeg import probe_duration

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    assert probe_duration(source) == pytest.approx(0.1)
    assert extract_audio(source, tmp_path / "result.wav").is_file()
    video = tmp_path / "video.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source), "-c:a", "aac", str(video),
    ], check=True)
    assert probe_duration(video) == pytest.approx(0.1, abs=0.05)
    assert extract_audio(video, tmp_path / "video-result.wav").is_file()
    # A disguised upload can contain a playlist instead of the advertised container.
    playlist = tmp_path / "playlist.wav"
    playlist.write_text(f"ffconcat version 1.0\nfile '{source}'\n")
    assert probe_duration(playlist) is None
    with pytest.raises(subprocess.CalledProcessError):
        extract_audio(playlist, tmp_path / "playlist-result.wav")
    assert not (tmp_path / "playlist-result.wav").exists()


@pytest.mark.parametrize("reference", ["file:///tmp/private.ts", "http://127.0.0.1/private.ts"])
def test_ffmpeg_rejects_hls_playlists_disguised_as_audio(tmp_path, reference):
    import shutil
    from echoscript.media.ffmpeg import probe_duration

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    source = tmp_path / "source.mp3"
    source.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{reference}\n#EXT-X-ENDLIST\n")
    assert probe_duration(source) is None
    with pytest.raises(subprocess.CalledProcessError):
        extract_audio(source, tmp_path / "audio.wav")


@pytest.mark.parametrize("previous", [None, b"previous complete audio"])
def test_extract_audio_publishes_only_after_ffmpeg_completes(tmp_path, monkeypatch, previous):
    source = tmp_path / "source.wav"
    output = tmp_path / "audio.wav"
    source.touch()
    if previous is not None:
        output.write_bytes(previous)

    def complete_conversion(command, check):
        partial = Path(command[-1])
        assert check is True
        assert partial.parent == output.parent
        assert partial != output
        assert partial.name.endswith(".part.wav")
        partial.write_bytes(b"unfinished")
        if previous is None:
            assert not output.exists()
        else:
            assert output.read_bytes() == previous
        partial.write_bytes(b"complete audio")

    monkeypatch.setattr("echoscript.media.ffmpeg.subprocess.run", complete_conversion)
    assert extract_audio(source, output, max_duration_seconds=1) == output
    assert output.read_bytes() == b"complete audio"
    assert not list(tmp_path.glob("*.part.wav"))


def test_extract_audio_preserves_existing_result_if_process_cannot_start(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    output = tmp_path / "audio.wav"
    source.touch()
    output.write_bytes(b"complete audio")
    with patch("echoscript.media.ffmpeg.subprocess.run", side_effect=OSError("Could not start ffmpeg")):
        with pytest.raises(OSError, match="Could not start"):
            extract_audio(source, output)
    assert output.read_bytes() == b"complete audio"
    assert not list(tmp_path.glob("*.part.wav"))


@pytest.mark.parametrize("extension,codec", [
    ("caf", "pcm_s16le"), ("ac3", "ac3"), ("eac3", "eac3"),
    ("dts", "dca"), ("w64", "pcm_s16le"),
])
def test_self_contained_audio_formats_remain_decodable(tmp_path, extension, codec):
    import shutil
    import wave
    from echoscript.media.ffmpeg import probe_duration

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    source = tmp_path / f"source.{extension}"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=0.12", "-ac", "2",
        "-c:a", codec, "-strict", "-2", str(source),
    ], check=True)
    assert 0.10 < probe_duration(source) < 0.20
    output = extract_audio(source, tmp_path / "audio.wav", max_duration_seconds=1)
    with wave.open(str(output), "rb") as audio:
        assert audio.getframerate() == 16000
        assert audio.getnchannels() == 1
        assert audio.getnframes() > 0
    assert not list(tmp_path.glob("*.part.wav"))
