import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from echoscript.media import download_public_url, validate_remote_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/audio.wav",
        "ftp://example.com/audio.wav",
        "http://localhost/audio.wav",
        "http://service.localhost/audio.wav",
        "http://127.0.0.1/audio.wav",
        "http://10.0.0.1/audio.wav",
        "http://169.254.169.254/latest/meta-data",
        "http://0.0.0.0/audio.wav",
        "http://224.0.0.1/audio.wav",
        "http://192.0.2.1/audio.wav",
        "http://[::1]/audio.wav",
    ],
)
def test_validate_remote_url_rejects_non_public_targets(url):
    with pytest.raises(ValueError):
        validate_remote_url(url)


def test_validate_remote_url_accepts_public_dns_result_without_network():
    answer = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ]
    with patch("echoscript.media.youtube.socket.getaddrinfo", return_value=answer) as resolve:
        url = "https://media.example/audio.wav"
        assert validate_remote_url(url) == url
    resolve.assert_called_once()


def test_validate_remote_url_rejects_hostname_with_any_private_dns_result():
    answer = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    with patch("echoscript.media.youtube.socket.getaddrinfo", return_value=answer):
        with pytest.raises(ValueError, match="non-public"):
            validate_remote_url("https://media.example/audio.wav")


def test_validate_remote_url_rejects_unresolvable_hostname():
    with patch(
        "echoscript.media.youtube.socket.getaddrinfo",
        side_effect=socket.gaierror("not found"),
    ):
        with pytest.raises(ValueError, match="Could not resolve"):
            validate_remote_url("https://missing.example/audio.wav")


def test_remote_download_enforces_limit_during_download(monkeypatch, tmp_path):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            captured["progress_hooks"][0]({"downloaded_bytes": 11})

    fake_module = type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)
    with pytest.raises(RuntimeError, match="size limit"):
        download_public_url("https://93.184.216.34/audio", tmp_path, max_bytes=10)
    assert captured["max_filesize"] == 10


def test_remote_download_checks_completed_file_size(monkeypatch, tmp_path):
    class FakeYoutubeDL:
        def __init__(self, options):
            self.path = Path(options["outtmpl"].replace("%(ext)s", "mp3"))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            self.path.write_bytes(b"x" * 11)
            return {"ext": "mp3"}

        def prepare_filename(self, _info):
            return str(self.path)

    fake_module = type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL})
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)
    with pytest.raises(RuntimeError, match="size limit"):
        download_public_url("https://93.184.216.34/audio", tmp_path, max_bytes=10)
    assert not (tmp_path / "source.mp3").exists()
