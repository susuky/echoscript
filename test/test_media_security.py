import socket
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
        "http://[::ffff:127.0.0.1]/audio.wav",
        "http://2130706433/audio.wav",
        "http://0x7f000001/audio.wav",
        "https://user:password@93.184.216.34/audio.wav",
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

    monkeypatch.setattr("echoscript.media._network.PublicYoutubeDL", FakeYoutubeDL)
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

    monkeypatch.setattr("echoscript.media._network.PublicYoutubeDL", FakeYoutubeDL)
    with pytest.raises(RuntimeError, match="size limit"):
        download_public_url("https://93.184.216.34/audio", tmp_path, max_bytes=10)
    assert not list(tmp_path.glob("*.mp3"))


@pytest.fixture
def public_media_server(monkeypatch):
    """Real local HTTP responses, reached only via a simulated public IP socket."""
    import io
    import threading
    import wave
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    content = buffer.getvalue()
    visited = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            if self.path == "/encoded":
                import gzip

                assert self.headers.get("Accept-Encoding") == "identity"
                body = gzip.compress(content * 16)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/public-redirect":
                self.send_response(302)
                self.send_header("Location", f"http://public.test:{self.server.server_port}/audio.wav")
                self.end_headers()
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/private")
                self.end_headers()
            elif self.path == "/embed":
                body = f'<html><script>videojs("player"); player.src({{"src":"http://127.0.0.1:{self.server.server_port}/private.m3u8","type":"application/x-mpegurl"}});</script></html>'.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_resolve = socket.getaddrinfo
    original_socket = socket.socket
    connects = []

    def resolve(host, port, *args, **kwargs):
        if host == "public.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return original_resolve(host, port, *args, **kwargs)

    class BridgedSocket(original_socket):
        def connect(self, address):
            connects.append(address)
            assert address[0] == "93.184.216.34", "An unchecked private connection escaped"
            return super().connect(("127.0.0.1", server.server_port))

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", BridgedSocket)
    try:
        yield f"http://public.test:{server.server_port}", content, visited, connects
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_public_download_uses_checked_socket_and_ignores_environment_proxy(public_media_server, monkeypatch, tmp_path):
    url, content, visited, connects = public_media_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    path = download_public_url(url + "/audio.wav", tmp_path)
    assert path.read_bytes() == content
    assert visited == ["/audio.wav", "/audio.wav"]
    assert connects and all(address[0] == "93.184.216.34" for address in connects)


@pytest.mark.parametrize("route", ["/redirect", "/embed"])
def test_remote_redirects_and_extracted_media_cannot_reach_private_host(public_media_server, tmp_path, route):
    from yt_dlp.networking.exceptions import RequestError

    url, _, visited, _ = public_media_server
    with pytest.raises(RequestError, match="non-public"):
        download_public_url(url + route, tmp_path)
    assert visited == [route]
    assert not list(tmp_path.glob("*.wav"))


def test_dns_rebinding_is_rejected_at_connect_time(monkeypatch, tmp_path):
    from yt_dlp.networking.exceptions import RequestError

    answers = iter([
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    ])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: next(answers))
    with patch("echoscript.media._network.socket.socket") as connect:
        with pytest.raises(RequestError, match="non-public"):
            download_public_url("http://public.test/audio.wav", tmp_path)
        connect.assert_not_called()


def test_streaming_downloader_cannot_bypass_checked_transport(monkeypatch, tmp_path):
    from echoscript.media._network import PublicYoutubeDL

    with PublicYoutubeDL({"quiet": True, "proxy": ""}) as downloader:
        for protocol in ("m3u8_native", "http_dash_segments", "rtmp"):
            with pytest.raises(ValueError, match="download this media locally first"):
                downloader.dl(str(tmp_path / "audio"), {"url": "http://public.test/audio", "protocol": protocol})


def test_socket_connect_does_not_resolve_checked_hostname_again(monkeypatch):
    from echoscript.media._network import _public_connection
    from unittest.mock import Mock

    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    resolve = Mock(return_value=answer)
    connection = Mock()
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", Mock(return_value=connection))
    assert _public_connection(("public.test", 443), timeout=5) is connection
    resolve.assert_called_once_with("public.test", 443, type=socket.SOCK_STREAM)
    connection.connect.assert_called_once_with(("93.184.216.34", 443))


def test_extractors_cannot_escape_the_supported_platform_set():
    from echoscript.media._network import PublicYoutubeDL

    with PublicYoutubeDL({"quiet": True, "proxy": ""}) as downloader:
        with pytest.raises(ValueError, match="Only public YouTube videos"):
            downloader.get_info_extractor("Vimeo")


@pytest.mark.parametrize("result_type", ["playlist", "multi_video"])
def test_public_download_rejects_playlists_before_processing_any_entry(result_type, monkeypatch):
    from echoscript.media._network import PublicYoutubeDL
    from yt_dlp import YoutubeDL

    with PublicYoutubeDL({"quiet": True, "proxy": ""}) as downloader:
        with patch.object(YoutubeDL, "process_ie_result") as process:
            with pytest.raises(ValueError, match="Only a single public video"):
                downloader.process_ie_result({"_type": result_type, "entries": [{"url": "http://public.test/audio.wav"}]})
            process.assert_not_called()


def test_public_redirect_remains_downloadable(public_media_server, tmp_path):
    url, content, visited, connects = public_media_server
    path = download_public_url(url + "/public-redirect", tmp_path)
    assert path.read_bytes() == content
    assert visited[0] == "/public-redirect"
    assert visited[-1] == "/audio.wav"
    assert all(address[0] == "93.184.216.34" for address in connects)


@pytest.mark.parametrize("encodings", [["gzip"], ["br"], ["deflate"], ["identity", "GZip"], ["identity, gzip"]])
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_compressed_response_is_closed_before_any_read_or_decompression(encodings, scheme):
    from email.message import Message
    from unittest.mock import Mock
    from echoscript.media._network import PublicHTTPHandler
    from yt_dlp.networking._urllib import HTTPHandler

    headers = Message()
    for encoding in encodings:
        headers.add_header("Content-Encoding", encoding)
    response = Mock(headers=headers)
    handler = PublicHTTPHandler()
    with patch.object(HTTPHandler, "http_response") as decompress:
        with pytest.raises(ValueError, match="Compressed HTTP responses are not supported"):
            getattr(handler, scheme + "_response")(Mock(), response)
        decompress.assert_not_called()
    response.read.assert_not_called()
    response.close.assert_called_once()


def test_server_cannot_override_identity_encoding_to_bypass_download_limit(public_media_server, tmp_path):
    from echoscript.media._network import PublicYoutubeDL
    from yt_dlp.networking.common import Request
    from yt_dlp.networking.exceptions import RequestError
    from yt_dlp.networking._urllib import HTTPHandler

    url, _, visited, _ = public_media_server
    with patch.object(HTTPHandler, "gz") as decompress:
        with pytest.raises(RequestError, match="Compressed HTTP responses are not supported"):
            download_public_url(url + "/encoded", tmp_path, max_bytes=32768)
        # Extractor-specific headers must not restore compression either.
        with PublicYoutubeDL({"quiet": True, "proxy": ""}) as downloader:
            with pytest.raises(RequestError, match="Compressed HTTP responses are not supported"):
                downloader.urlopen(Request(url + "/encoded", headers={"Accept-Encoding": "gzip"}))
        decompress.assert_not_called()
    assert visited == ["/encoded", "/encoded"]
    assert not list(tmp_path.iterdir())
