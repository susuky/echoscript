from __future__ import annotations

import ipaddress
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def validate_remote_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("Only http(s) URLs are supported")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local and non-public URLs are not supported")

    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except (socket.gaierror, UnicodeError) as exc:
            raise ValueError(f"Could not resolve URL hostname: {hostname}") from exc
        addresses = list({ipaddress.ip_address(item[4][0]) for item in resolved})

    if not addresses or any(
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
        for address in addresses
    ):
        raise ValueError("Local and non-public URLs are not supported")
    return url


def download_public_url(
    url: str,
    output_dir: str | Path,
    *,
    max_bytes: int = 2 * 1024**3,
) -> Path:
    """Download public media through yt-dlp.

    Authenticated YouTube is intentionally not handled here: browser cookies belong on
    the client machine. The CLI can download with local browser cookies and upload the file.
    """
    validate_remote_url(url)
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise RuntimeError("URL ingestion requires yt-dlp") from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    template = str(output_dir / "source.%(ext)s")

    def enforce_size_limit(status: dict) -> None:
        observed = max(
            int(status.get("downloaded_bytes") or 0),
            int(status.get("total_bytes") or 0),
            int(status.get("total_bytes_estimate") or 0),
        )
        if observed > max_bytes:
            raise RuntimeError("Remote media exceeds configured size limit")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes,
        "progress_hooks": [enforce_size_limit],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        matches = list(output_dir.glob("source.*"))
        if not matches:
            raise RuntimeError("yt-dlp completed but no downloaded media was found")
        path = matches[0]
    if path.stat().st_size > max_bytes:
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
    max_bytes: int = 2 * 1024**3,
) -> Path:
    """Client-side authenticated download for membership/login-required videos."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    validate_remote_url(url)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    browser_arg = f"{browser}:{profile}" if profile else browser
    template = str(output_dir / "source.%(ext)s")
    cmd = [
        yt_dlp_bin,
        "--no-playlist",
        "--max-filesize",
        str(max_bytes),
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
    path: Path | None = None
    if printed:
        candidate = Path(printed[-1])
        if candidate.exists() and candidate.resolve().parent == output_dir.resolve():
            path = candidate
    if path is None:
        matches = list(output_dir.glob("source.*"))
        if not matches:
            raise RuntimeError("yt-dlp completed but no downloaded media was found")
        path = matches[0]
    if path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded media exceeds the client size limit of {max_bytes} bytes"
        )
    return path
