import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from echoscript.cli import cli
from echoscript.media import download_with_browser_cookies


PUBLIC_URL = "https://93.184.216.34/video"


def test_browser_cookie_download_passes_binary_profile_and_size_limit(tmp_path):
    media = tmp_path / "source.webm"
    media.write_bytes(b"audio")
    completed = subprocess.CompletedProcess([], 0, stdout=f"{media}\n", stderr="")

    with patch("echoscript.media.youtube.subprocess.run", return_value=completed) as run:
        result = download_with_browser_cookies(
            PUBLIC_URL,
            tmp_path,
            browser="chrome",
            profile="Profile 2",
            yt_dlp_bin="/opt/yt-dlp",
            max_bytes=1234,
        )

    assert result == media
    command = run.call_args.args[0]
    assert command[0] == "/opt/yt-dlp"
    assert command[command.index("--max-filesize") + 1] == "1234"
    assert command[command.index("--cookies-from-browser") + 1] == "chrome:Profile 2"
    run.assert_called_once_with(command, check=True, capture_output=True, text=True)


def test_browser_cookie_download_deletes_oversized_result(tmp_path):
    media = tmp_path / "source.webm"
    media.write_bytes(b"too-large")
    completed = subprocess.CompletedProcess([], 0, stdout=f"{media}\n", stderr="")

    with patch("echoscript.media.youtube.subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="client size limit"):
            download_with_browser_cookies(
                PUBLIC_URL,
                tmp_path,
                browser="chrome",
                max_bytes=3,
            )

    assert not media.exists()


def test_browser_cookie_download_hides_subprocess_details(tmp_path):
    failure = subprocess.CalledProcessError(
        7,
        ["yt-dlp", "--cookies-from-browser", "chrome:Secret Profile", "secret-url"],
        stderr="cookie database: /home/owner/.config/google-chrome/Secret Profile",
    )
    with patch("echoscript.media.youtube.subprocess.run", side_effect=failure):
        with pytest.raises(RuntimeError) as raised:
            download_with_browser_cookies(
                PUBLIC_URL,
                tmp_path,
                browser="chrome",
                profile="Secret Profile",
            )

    message = str(raised.value)
    assert "exit code 7" in message
    assert "Secret Profile" not in message
    assert "/home/owner" not in message
    assert "secret-url" not in message


def test_download_command_forwards_options_and_keeps_local_file(tmp_path):
    media = tmp_path / "source.webm"
    media.write_bytes(b"audio")
    with patch("echoscript.cli.download_with_browser_cookies", return_value=media) as download:
        result = CliRunner().invoke(
            cli,
            [
                "download",
                "https://www.youtube.com/watch?v=members-only",
                "--browser",
                "chrome",
                "--profile",
                "Profile 2",
                "--yt-dlp-bin",
                "/opt/yt-dlp",
                "--max-download-bytes",
                "1234",
                "--output-dir",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert str(media.resolve()) in result.output
    download.assert_called_once()
    assert download.call_args.args == (
        "https://www.youtube.com/watch?v=members-only",
        tmp_path,
    )
    kwargs = download.call_args.kwargs
    assert kwargs == {
        "browser": "chrome",
        "profile": "Profile 2",
        "yt_dlp_bin": "/opt/yt-dlp",
        "max_bytes": 1234,
    }
