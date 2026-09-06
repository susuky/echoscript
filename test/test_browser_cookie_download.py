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


def test_download_template_distinguishes_media_and_never_returns_unrelated_old_file(tmp_path):
    old_file = tmp_path / "source.webm"
    old_file.write_bytes(b"old video")
    commands = []

    def download(command, **_kwargs):
        commands.append(command)
        template = command[command.index("-o") + 1]
        assert "%(extractor_key)s" in template
        assert "%(id)s" in template
        media = template.replace("%(extractor_key)s", "Generic").replace("%(id)s", "video").replace("%(ext)s", "webm")
        from pathlib import Path
        Path(media).write_bytes(command[-1].encode())
        return subprocess.CompletedProcess(command, 0, stdout=media + "\n")

    with patch("echoscript.media.youtube.subprocess.run", side_effect=download):
        first = download_with_browser_cookies(PUBLIC_URL, tmp_path, browser="chrome")
        second = download_with_browser_cookies(PUBLIC_URL + "-other", tmp_path, browser="chrome")
    assert first != second
    assert first.read_bytes() != second.read_bytes()
    assert old_file.read_bytes() == b"old video"
    with patch("echoscript.media.youtube.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="")):
        with pytest.raises(RuntimeError, match="no downloaded media"):
            download_with_browser_cookies(PUBLIC_URL, tmp_path, browser="chrome")


def test_browser_download_without_size_limit(tmp_path):
    media = tmp_path / 'source.webm'
    with media.open('wb') as output:
        output.truncate(2 * 1024**3 + 1)
    completed = subprocess.CompletedProcess([], 0, stdout=f'{media}\n', stderr='')
    with patch('echoscript.media.youtube.subprocess.run', return_value=completed) as run:
        assert download_with_browser_cookies(PUBLIC_URL, tmp_path, browser='chrome') == media
    assert '--max-filesize' not in run.call_args.args[0]


def test_server_browser_login_is_youtube_only(tmp_path):
    from http.cookiejar import Cookie, CookieJar
    from echoscript.media.youtube import download_public_url
    jar = CookieJar()
    for domain in ['.youtube.com', '.accounts.google.com', '.example.com']:
        jar.set_cookie(Cookie(0, 'session', 'secret', None, False, domain, True,
                              True, '/', True, True, None, True, None, None, {}))
    media = tmp_path / 'source.webm'
    media.write_bytes(b'audio')
    with patch('echoscript.media._network.PublicYoutubeDL') as factory:
        downloader = factory.return_value.__enter__.return_value
        downloader.cookiejar = jar
        downloader.extract_info.return_value = {'filepath': str(media)}
        download_public_url('https://youtu.be/abcdefghijk?t=3', tmp_path,
                            browser='chrome', profile='Default')
        assert factory.call_args.args[0]['cookiesfrombrowser'] == ('chrome', 'Default', None, None)
        assert {c.domain for c in jar} == {'.youtube.com'}
        assert downloader.extract_info.call_args.args[0] == 'https://www.youtube.com/watch?v=abcdefghijk'
        download_public_url(PUBLIC_URL, tmp_path, browser='chrome', profile='Default')
        assert 'cookiesfrombrowser' not in factory.call_args.args[0]


@pytest.mark.parametrize('url', ['https://youtube.com.evil.com/watch?v=abcdefghijk',
                                'https://example.com/abcdefghijk',
                                'https://www.youtube.com/playlist?list=abc'])
def test_non_video_urls_never_use_browser_login(url):
    from echoscript.media.youtube import _youtube_video_url
    assert _youtube_video_url(url) is None


@pytest.mark.parametrize('keyring', [None, 'gnomekeyring'])
def test_server_login_settings_reach_worker_and_cookie_reader(tmp_path, monkeypatch, keyring):
    from http.cookiejar import CookieJar
    from echoscript.config import Settings
    from echoscript.pipeline.pipeline import TranscriptionPipeline
    from echoscript.web import LocalJobController
    from yt_dlp.cookies import _parse_browser_specification

    monkeypatch.setenv('ECHOSCRIPT_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('ECHOSCRIPT_YOUTUBE_BROWSER', 'chrome')
    monkeypatch.setenv('ECHOSCRIPT_YOUTUBE_BROWSER_PROFILE', '/home/ping/.config/google-chrome/Default')
    monkeypatch.setenv('ECHOSCRIPT_YOUTUBE_BROWSER_KEYRING', keyring or '')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')
    settings = Settings.from_env()
    controller = LocalJobController(settings)
    # A later parent-environment change must not override the loaded settings.
    monkeypatch.setenv('ECHOSCRIPT_YOUTUBE_BROWSER_KEYRING', 'kwallet')
    environment = controller._worker_environment()
    assert environment['ECHOSCRIPT_YOUTUBE_BROWSER_KEYRING'] == (keyring or '')
    assert environment['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/run/user/1000/bus'
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    worker_settings = Settings.from_env()
    assert worker_settings.youtube_browser_keyring == keyring
    directory = tmp_path / 'job'
    directory.mkdir()
    media = directory / 'download.webm'
    media.write_bytes(b'audio')
    pipeline = TranscriptionPipeline(worker_settings, None)
    with patch('echoscript.media.youtube.validate_remote_url'), patch('echoscript.media._network.PublicYoutubeDL') as factory:
        downloader = factory.return_value.__enter__.return_value
        downloader.cookiejar = CookieJar()
        downloader.extract_info.return_value = {'filepath': str(media)}
        assert pipeline._resolve_media({'source_type': 'url', 'source_value': 'https://youtu.be/abcdefghijk'}, directory) == media
        spec = factory.call_args.args[0]['cookiesfrombrowser']
        assert spec == ('chrome', settings.youtube_browser_profile, 'GNOMEKEYRING' if keyring else None, None)
        # Exercise the actual installed yt-dlp API, which rejects lowercase keyrings.
        assert _parse_browser_specification(*spec)[2] == spec[2]
