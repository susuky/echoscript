from __future__ import annotations

import re
import hashlib
import ipaddress
import socket
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def validate_remote_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("Only http(s) URLs are supported")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local and non-public URLs are not supported")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in media URLs are not supported")
    _resolve_public_addresses(hostname, parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    return url


def _resolve_public_addresses(hostname: str, port: int) -> list:
    """Resolve once; the caller connects to these exact checked addresses."""
    try:
        resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValueError(f"Could not resolve URL hostname: {hostname}") from exc
    for item in resolved:
        address = ipaddress.ip_address(item[4][0])
        if (not address.is_global or address.is_private or address.is_loopback
                or address.is_link_local or address.is_reserved or address.is_unspecified
                or address.is_multicast or getattr(address, "ipv4_mapped", None)
                or getattr(address, "sixtofour", None) or getattr(address, "teredo", None)):
            raise ValueError("Local and non-public URLs are not supported")
    if not resolved:
        raise ValueError("Local and non-public URLs are not supported")
    return resolved


def _output_template(url: str, output_dir: Path) -> str:
    identity = hashlib.sha256(url.encode()).hexdigest()[:12]
    return str(output_dir / f"source-%(extractor_key)s-%(id)s-{identity}.%(ext)s")


def _completed_download(path: Path, output_dir: Path) -> Path:
    if not path.is_file() or path.resolve().parent != output_dir.resolve():
        raise RuntimeError("yt-dlp completed but no downloaded media was found")
    return path


def _youtube_video_url(url: str) -> str | None:
    """Only send the configured account through a canonical single-video URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    video_id = None
    if host == "youtu.be":
        video_id = parsed.path.removeprefix("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            video_id = parsed.path.split("/")[2]
    if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return "https://www.youtube.com/watch?v=" + video_id
    return None


def download_public_url(
    url: str,
    output_dir: str | Path,
    *,
    max_bytes: int = 0,
    browser: str | None = None,
    profile: str | None = None,
    keyring: str | None = None,
) -> Path:
    """Download public YouTube or direct HTTP(S) media with checked socket connections.

    An explicitly configured browser enables login for YouTube videos only.
    Unsupported streaming manifests still require a client-side download.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    validate_remote_url(url)
    try:
        from ._network import PublicYoutubeDL
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise RuntimeError("URL ingestion requires yt-dlp") from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    template = _output_template(url, output_dir)

    def enforce_size_limit(status: dict) -> None:
        observed = max(
            int(status.get("downloaded_bytes") or 0),
            int(status.get("total_bytes") or 0),
            int(status.get("total_bytes_estimate") or 0),
        )
        if max_bytes > 0 and observed > max_bytes:
            raise RuntimeError("Remote media exceeds configured size limit")

    opts = {
        "format": "bestaudio[protocol=https]/bestaudio[protocol=http]/best[protocol=https]/best[protocol=http]",
        "allowed_extractors": ["youtube$", "generic$"],
        "proxy": "",
        "fixup": "never",
        "socket_timeout": 30,
        "retries": 2,
        "cachedir": False,
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes or None,
        "progress_hooks": [enforce_size_limit],
    }
    authenticated_url = _youtube_video_url(url) if browser else None
    if authenticated_url:
        opts["cookiesfrombrowser"] = (browser, profile, keyring.upper() if keyring else None, None)
    with PublicYoutubeDL(opts) as ydl:
        if authenticated_url:
            # Browser extraction can contain unrelated accounts; retain YouTube only.
            for cookie in list(ydl.cookiejar):
                domain = cookie.domain.lstrip(".").lower()
                if domain != "youtube.com" and not domain.endswith(".youtube.com"):
                    ydl.cookiejar.clear(cookie.domain, cookie.path, cookie.name)
        info = ydl.extract_info(authenticated_url or url, download=True)
        if not info or info.get("_type", "video") != "video":
            raise ValueError("Only public YouTube videos and direct HTTP(S) media files are supported")
        path = _completed_download(Path(info.get("filepath") or ydl.prepare_filename(info)), output_dir)
    if max_bytes > 0 and path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        raise RuntimeError("Remote media exceeds configured size limit")
    return path


def download_with_browser_cookies(
    url: str,
    output_dir: str | Path,
    *,
    browser: str,
    profile: str | None = None,
    yt_dlp_bin: str = "yt-dlp",
    max_bytes: int = 0,
) -> Path:
    """Client-side authenticated download for membership/login-required videos."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    validate_remote_url(url)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    browser_arg = f"{browser}:{profile}" if profile else browser
    template = _output_template(url, output_dir)
    cmd = [
        yt_dlp_bin,
        "--no-playlist",
        *(["--max-filesize", str(max_bytes)] if max_bytes else []),
        "--cookies-from-browser",
        browser_arg,
        "-f",
        "bestaudio/best",
        "-o",
        template,
        "--print",
        "after_move:filepath",
        url,
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"yt-dlp failed with exit code {exc.returncode}. "
            "Check the URL and browser/profile access, then retry."
        ) from None

    printed = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not printed:
        raise RuntimeError("yt-dlp completed but no downloaded media was found")
    path = _completed_download(Path(printed[-1]), output_dir)
    if max_bytes > 0 and path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded media exceeds the client size limit of {max_bytes} bytes"
        )
    return path
