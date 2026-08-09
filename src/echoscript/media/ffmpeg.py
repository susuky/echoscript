from __future__ import annotations

import json
import subprocess
from pathlib import Path


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
    cmd.extend([
        "-c:a",
        "pcm_s16le",
        str(output),
    ])
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        output.unlink(missing_ok=True)
        raise
    if max_duration_seconds is not None and max_duration_seconds > 0:
        # mono PCM s16le is 2 bytes/sample. Allow generous space for the WAV
        # container header/metadata without permitting unbounded output growth.
        max_output_bytes = int(max_duration_seconds * sample_rate * 2) + 1024 * 1024
        if output.stat().st_size > max_output_bytes:
            output.unlink(missing_ok=True)
            raise RuntimeError("Extracted PCM audio exceeds configured size limit")
    return output


def probe_duration(source: str | Path, *, ffprobe_bin: str = "ffprobe") -> float | None:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
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
