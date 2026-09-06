from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


# Self-contained media containers only: playlists/concat can reference other files.
_INPUT_OPTIONS = [
    "-protocol_whitelist", "file,pipe",
    "-format_whitelist",
    "aac,ac3,aiff,amr,ape,asf,avi,caf,dts,eac3,flac,flv,matroska,webm,"
    "mov,mp4,m4a,3gp,3g2,mj2,mp3,mpeg,mpegts,ogg,w64,wav,wv",
]


def extract_audio(
    source: str | Path,
    output: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    sample_rate: int = 16000,
    max_duration_seconds: float | None = None,
) -> Path:
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *_INPUT_OPTIONS,
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
    ]
    if max_duration_seconds is not None and max_duration_seconds > 0:
        cmd.extend(["-t", str(max_duration_seconds)])
    # A killed worker must not leave a truncated file that the retry treats as
    # complete. Unique names also isolate a still-exiting ffmpeg from a retry.
    with tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}-", suffix=".part.wav", dir=output.parent, delete=False,
    ) as temporary:
        partial = Path(temporary.name)
    cmd.extend(["-c:a", "pcm_s16le", str(partial)])
    try:
        subprocess.run(cmd, check=True)
        if max_duration_seconds is not None and max_duration_seconds > 0:
            # mono PCM s16le is 2 bytes/sample; allow container metadata overhead.
            max_output_bytes = int(max_duration_seconds * sample_rate * 2) + 1024 * 1024
            if partial.stat().st_size > max_output_bytes:
                raise RuntimeError("Extracted PCM audio exceeds configured size limit")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return output


def probe_duration(source: str | Path, *, ffprobe_bin: str = "ffprobe") -> float | None:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        *_INPUT_OPTIONS,
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(completed.stdout)
        return float(data["format"]["duration"])
    except (subprocess.CalledProcessError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
