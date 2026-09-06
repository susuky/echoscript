from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable

from echoscript.config import Settings
from echoscript.media import validate_remote_url
from echoscript.render import render_transcript
from echoscript.schema import JobOptions, Transcript
from echoscript.storage import JobStore


_PRIVATE_JOB_FIELDS = {"media_path", "audio_path", "result_path", "worker_pid"}
_CLEANUP_INTERVAL_SECONDS = 3600.0
_STALE_UPLOAD_SECONDS = 3600.0
_MAX_JOB_CRASH_RETRIES = 3
log = logging.getLogger("echoscript.web")

def _acquire_dispatcher_lock(path: str | Path) -> IO[str]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(
            f"Another echoscript web dispatcher holds {lock_path}"
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


class LocalJobController:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: JobStore | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ):
        self.settings = settings or Settings.from_env()
        self.settings.ensure_directories()
        self.store = store or JobStore(self.settings.db_path)
        self._popen_factory = popen_factory
        self._process: Any | None = None
        self._active_job_id: str | None = None
        self._crash_retries: dict[str, int] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._dispatcher_lock: IO[str] | None = None
        self._last_cleanup = time.monotonic() - _CLEANUP_INTERVAL_SECONDS

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key not in _PRIVATE_JOB_FIELDS}

    def _options(self, options: JobOptions | dict[str, Any]) -> JobOptions:
        normalized = options if isinstance(options, JobOptions) else JobOptions.from_dict(options)
        if normalized.diarize and not self.settings.hf_token:
            raise ValueError("Speaker diarization requires HF_TOKEN")
        return normalized

    def begin_upload(self, filename: str, options: JobOptions | dict[str, Any]) -> dict[str, Any]:
        job = self.store.create_job(
            source_type="upload", source_value=Path(filename).name,
            media_path=None, options=self._options(options), initial_status="uploading",
        )
        return self._public_job(job)

    def submit_upload(
        self,
        source: str | Path,
        options: JobOptions | dict[str, Any],
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser()
        if not source_path.is_file():
            raise ValueError("Uploaded media file does not exist")
        job = self.begin_upload(source_path.name, options)
        job_dir = self.settings.jobs_dir / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_path.suffix[:16]
        destination = job_dir / f"upload{suffix}"
        partial = job_dir / f"upload{suffix}.part"
        try:
            copied = 0
            with source_path.open("rb") as source_file, partial.open("wb") as output:
                while chunk := source_file.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > self.settings.max_upload_bytes:
                        raise ValueError("Upload exceeds configured size limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(partial, destination)
            self.store.finish_upload(job["id"], str(destination))
        except Exception as exc:
            partial.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            self.store.fail(job["id"], f"Upload failed: {type(exc).__name__}: {exc}")
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        self._wake.set()
        return self.job(job["id"])

    def submit_url(
        self,
        url: str,
        options: JobOptions | dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self._options(options)
        validated = validate_remote_url(url)
        job = self.store.create_job(
            source_type="url",
            source_value=validated,
            media_path=None,
            options=normalized,
        )
        self._wake.set()
        return self._public_job(job)

    def job(self, job_id: str) -> dict[str, Any]:
        return self._public_job(self.store.get_job(job_id))

    def latest_job_id(self) -> str | None:
        jobs = self.store.list_jobs(limit=1)
        return str(jobs[0]["id"]) if jobs else None

    def result(self, job_id: str) -> tuple[str, list[str]]:
        job = self.store.get_job(job_id)
        if job["status"] != "done":
            raise ValueError(f"Job is {job['status']}")
        output_dir = (self.settings.jobs_dir / job_id / "output").resolve()
        jobs_root = self.settings.jobs_dir.resolve()
        if output_dir.parent.parent != jobs_root:
            raise ValueError("Invalid job output path")
        paths = [
            output_dir / f"result.{fmt}"
            for fmt in ("txt", "srt", "vtt", "json")
            if (output_dir / f"result.{fmt}").is_file()
        ]
        if any(path.resolve().parent != output_dir for path in paths):
            raise ValueError("Invalid job output path")
        text_path = output_dir / "result.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        canonical_path = output_dir / "result.json"
        if canonical_path.is_file():
            try:
                payload = json.loads(canonical_path.read_text(encoding="utf-8"))
                if payload.get("speakers"):
                    text = render_transcript(Transcript.from_dict(payload), "txt")
                elif not text:
                    text = str(payload.get("text") or "")
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass
        return text, [str(path) for path in paths]

    def _handle_worker_crash(self, job_ids: set[str]) -> None:
        for job_id in job_ids:
            attempts = self._crash_retries.get(job_id, 0) + 1
            if attempts >= _MAX_JOB_CRASH_RETRIES:
                self.store.fail_active_job(
                    job_id,
                    f"Worker exited abnormally {_MAX_JOB_CRASH_RETRIES} times",
                )
                self._crash_retries.pop(job_id, None)
                continue
            self._crash_retries[job_id] = attempts
            self.store.requeue_running_job(
                job_id,
                stage=f"worker_crashed_retry_{attempts}",
            )

    def _cleanup_stale_uploads(self) -> None:
        cutoff = time.time() - _STALE_UPLOAD_SECONDS
        root = self.settings.jobs_dir.resolve()
        for job_id in self.store.stale_uploading_job_ids(cutoff):
            if not self.store.fail_stale_upload(
                job_id,
                cutoff,
                "Upload did not complete before the stale timeout",
            ):
                continue
            job_dir = (root / job_id).resolve()
            if job_dir.parent != root:
                log.error("refusing to clean unsafe stale upload path for id %r", job_id)
                continue
            shutil.rmtree(job_dir, ignore_errors=True)

    def _maybe_cleanup_expired_jobs(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_INTERVAL_SECONDS:
            return
        self._cleanup_stale_uploads()
        from echoscript.worker.worker import _cleanup_expired_jobs

        _cleanup_expired_jobs(
            self.store,
            self.settings.jobs_dir,
            self.settings.job_retention_days,
        )
        self._last_cleanup = now

    def _recover_if_worker_idle(self) -> bool:
        from echoscript.worker.worker import _acquire_worker_lock

        try:
            handle = _acquire_worker_lock(
                self.settings.worker_lock_path or self.settings.data_dir / "worker.lock"
            )
        except RuntimeError:
            return False
        try:
            if self.store.running_job_ids():
                self.store.recover_running_jobs()
        finally:
            handle.close()
        return True

    def _worker_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        settings = self.settings
        env.update({
            "ECHOSCRIPT_DATA_DIR": str(settings.data_dir),
            "ECHOSCRIPT_DB_PATH": str(settings.db_path),
            "ECHOSCRIPT_JOBS_DIR": str(settings.jobs_dir),
            "ECHOSCRIPT_WORKER_LOCK_PATH": str(settings.worker_lock_path or settings.data_dir / "worker.lock"),
            "ECHOSCRIPT_RELEASE_BETWEEN_STAGES": str(settings.release_between_stages),
            "ECHOSCRIPT_MAX_MEDIA_DURATION_SECONDS": str(settings.max_media_duration_seconds),
            "ECHOSCRIPT_MAX_REMOTE_DOWNLOAD_BYTES": str(settings.max_remote_download_bytes),
            "ECHOSCRIPT_FFMPEG_BIN": settings.ffmpeg_bin,
            "ECHOSCRIPT_FFPROBE_BIN": settings.ffprobe_bin,
        })
        if settings.hf_token:
            env["HF_TOKEN"] = settings.hf_token
        return env

    def _supervise_once(self) -> None:
        self._maybe_cleanup_expired_jobs()
        if self._process is not None:
            return_code = self._process.poll()
            if return_code is None:
                return
            self._process.wait(timeout=0)
            running = (set(self.store.running_job_ids(worker_pid=self._process.pid))
                       if return_code != 75 else set())
            self._process = None
            if self._active_job_id is not None and return_code != 75:
                try:
                    initial_status = self.store.get_job(self._active_job_id)["status"]
                except KeyError:
                    initial_status = None
                if initial_status == "running" or (return_code != 0 and initial_status == "queued"):
                    running.add(self._active_job_id)
            if running:
                self._handle_worker_crash(running)
            elif self._active_job_id is not None:
                self._crash_retries.pop(self._active_job_id, None)
            self._active_job_id = None
        if not self._recover_if_worker_idle():
            return
        next_job_id = self.store.next_queued_job_id()
        if next_job_id is None:
            return
        with self._thread_lock:
            if self._stop.is_set():
                return
            self._active_job_id = next_job_id
            try:
                self._process = self._popen_factory(
                    [sys.executable, "-m", "echoscript.cli", "worker",
                     "--idle-timeout", str(self.settings.model_idle_timeout_seconds), "--job-id", next_job_id],
                    start_new_session=True,
                    env=self._worker_environment(),
                )
            except Exception:
                self._handle_worker_crash({next_job_id})
                self._active_job_id = None
                raise

    def _supervisor_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._supervise_once()
            except Exception:
                log.exception("worker supervisor iteration failed")
            self._wake.wait(0.5)
            self._wake.clear()

    def start(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._dispatcher_lock = _acquire_dispatcher_lock(
                self.settings.data_dir / "dispatcher.lock"
            )
            try:
                self._recover_if_worker_idle()
                self._cleanup_stale_uploads()
            except Exception:
                self._dispatcher_lock.close()
                self._dispatcher_lock = None
                raise
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._supervisor_loop,
                name="echoscript-worker-supervisor",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._dispatcher_lock.close()
                self._dispatcher_lock = None
                self._thread = None
                raise

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        with self._thread_lock:
            try:
                process = self._process
                if process is not None:
                    if process.poll() is None:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=5)
                    else:
                        process.wait(timeout=0)
            finally:
                self._process = None
                if self._dispatcher_lock is not None:
                    self._dispatcher_lock.close()
                    self._dispatcher_lock = None


def create_web_app(controller: LocalJobController | None = None):
    from echoscript.api import create_api

    return create_api(controller or LocalJobController())


def run_web() -> None:
    import uvicorn

    uvicorn.run(
        create_web_app(),
        host=os.getenv("ECHOSCRIPT_WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("ECHOSCRIPT_WEB_PORT", "7860")),
    )
