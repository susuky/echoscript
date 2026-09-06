from __future__ import annotations

import os
import math
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _huggingface_token() -> str | None:
    """Use an explicit token first, then the standard Hugging Face CLI cache."""
    explicit = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if explicit:
        return explicit
    try:
        from huggingface_hub import get_token
    except ImportError:
        return None
    return get_token()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    jobs_dir: Path
    release_between_stages: bool = True
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    max_upload_bytes: int = 0
    max_remote_download_bytes: int = 0
    max_media_duration_seconds: int = 43_200
    job_retention_days: int = 30
    worker_lock_path: Path | None = None
    hf_token: str | None = None
    model_idle_timeout_seconds: float = 300
    youtube_browser: str | None = None
    youtube_browser_profile: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("ECHOSCRIPT_DATA_DIR", "~/.local/share/echoscript")).expanduser().resolve()
        idle_timeout = float(os.getenv("ECHOSCRIPT_MODEL_IDLE_TIMEOUT_SECONDS", "300"))
        if not math.isfinite(idle_timeout) or idle_timeout < 0:
            raise ValueError("ECHOSCRIPT_MODEL_IDLE_TIMEOUT_SECONDS must be finite and nonnegative")
        return cls(
            data_dir=root,
            db_path=Path(os.getenv("ECHOSCRIPT_DB_PATH", root / "jobs.sqlite3")).expanduser().resolve(),
            jobs_dir=Path(os.getenv("ECHOSCRIPT_JOBS_DIR", root / "jobs")).expanduser().resolve(),
            release_between_stages=_env_bool("ECHOSCRIPT_RELEASE_BETWEEN_STAGES", True),
            ffmpeg_bin=os.getenv("ECHOSCRIPT_FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=os.getenv("ECHOSCRIPT_FFPROBE_BIN", "ffprobe"),
            max_upload_bytes=int(os.getenv("ECHOSCRIPT_MAX_UPLOAD_BYTES", "0")),
            max_remote_download_bytes=int(
                os.getenv("ECHOSCRIPT_MAX_REMOTE_DOWNLOAD_BYTES", "0")
            ),
            max_media_duration_seconds=int(
                os.getenv("ECHOSCRIPT_MAX_MEDIA_DURATION_SECONDS", "43200")
            ),
            job_retention_days=int(os.getenv("ECHOSCRIPT_JOB_RETENTION_DAYS", "30")),
            worker_lock_path=Path(
                os.getenv("ECHOSCRIPT_WORKER_LOCK_PATH", root / "worker.lock")
            ).expanduser().resolve(),
            hf_token=_huggingface_token(),
            model_idle_timeout_seconds=idle_timeout,
            youtube_browser=os.getenv("ECHOSCRIPT_YOUTUBE_BROWSER") or None,
            youtube_browser_profile=os.getenv("ECHOSCRIPT_YOUTUBE_BROWSER_PROFILE") or None,
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.worker_lock_path is not None:
            self.worker_lock_path.parent.mkdir(parents=True, exist_ok=True)
