"""Per-downloader HTTP transport: validate DNS and connect without a second lookup."""
from __future__ import annotations

import http.client
import socket
import urllib.request
from functools import partial

from yt_dlp import YoutubeDL
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.http import HttpFD
from yt_dlp.networking._urllib import HTTPHandler, RedirectHandler, UrllibRH

from .youtube import _resolve_public_addresses


def _public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    addresses = _resolve_public_addresses(*address)
    last_error = None
    for family, kind, protocol, _, sockaddr in addresses:
        connection = socket.socket(family, kind, protocol)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            connection.close()
            last_error = exc
    raise last_error or OSError("Could not connect to public media host")


def _http_connection(connection_class, *args, **kwargs):
    connection = connection_class(*args, **kwargs)
    # Keep the original host for Host and HTTPS certificate / SNI validation.
    connection._create_connection = _public_connection
    return connection


class PublicHTTPHandler(HTTPHandler):
    def http_response(self, request, response):
        # yt-dlp's parent handler eagerly reads/decompresses entire responses.
        # Reject servers that ignore identity before that allocation can happen.
        if any(
            encoding.strip().lower() not in {"", "identity"}
            for value in response.headers.get_all("Content-Encoding", [])
            for encoding in value.split(",")
        ):
            response.close()
            raise ValueError("Compressed HTTP responses are not supported; download this media locally first")
        return super().http_response(request, response)

    https_response = http_response

    def http_open(self, request):
        return self.do_open(partial(_http_connection, http.client.HTTPConnection), request)

    def https_open(self, request):
        return self.do_open(
            partial(_http_connection, http.client.HTTPSConnection), request, context=self._context,
        )


class PublicUrllibRH(UrllibRH):
    _SUPPORTED_URL_SCHEMES = ("http", "https")
    _SUPPORTED_PROXY_SCHEMES = ()

    def _prepare_headers(self, request, headers):
        headers["Accept-Encoding"] = "identity"

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        # Deliberately no proxy, FTP, file or data handlers, including on redirects.
        opener = urllib.request.OpenerDirector()
        for handler in (
            PublicHTTPHandler(context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support)),
            urllib.request.HTTPCookieProcessor(cookiejar),
            urllib.request.UnknownHandler(),
            urllib.request.HTTPDefaultErrorHandler(),
            urllib.request.HTTPErrorProcessor(),
            RedirectHandler(),
        ):
            opener.add_handler(handler)
        opener.addheaders = []
        return opener


class PublicYoutubeDL(YoutubeDL):
    def get_info_extractor(self, ie_key):
        if ie_key not in {"Youtube", "Generic"}:
            raise ValueError("Only public YouTube videos and direct HTTP(S) media files are supported")
        return super().get_info_extractor(ie_key)

    def process_ie_result(self, ie_result, download=True, extra_info=None):
        # noplaylist only selects a video for some URLs; it is not a playlist ban.
        if ie_result.get("_type") in {"playlist", "multi_video"}:
            raise ValueError("Only a single public video or direct HTTP(S) media file is supported")
        return super().process_ie_result(ie_result, download=download, extra_info=extra_info)

    def build_request_director(self, handlers, preferences=None):
        # Every extractor request and redirect uses this transport, with no fallback.
        return super().build_request_director([PublicUrllibRH])

    def dl(self, name, info, subtitle=False, test=False):
        # HLS may silently delegate to ffmpeg even with hls_prefer_native enabled.
        if get_suitable_downloader(info, self.params) is not HttpFD:
            raise ValueError("Only direct HTTP(S) media downloads are supported; download this media locally first")
        return super().dl(name, info, subtitle=subtitle, test=test)
