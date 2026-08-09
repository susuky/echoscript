from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable

from echoscript.config import Settings
from echoscript.media import validate_remote_url
from echoscript.render import render_transcript
from echoscript.schema import JobOptions, Transcript
from echoscript.storage import JobStore


_PRIVATE_JOB_FIELDS = {"media_path", "audio_path", "result_path"}
_CLEANUP_INTERVAL_SECONDS = 3600.0
_STALE_UPLOAD_SECONDS = 3600.0
_MAX_JOB_CRASH_RETRIES = 3
log = logging.getLogger("echoscript.web")

_WEB_MODEL_CHOICES = {
    "qwen": [
        "Qwen/Qwen3-ASR-1.7B",
        "Qwen/Qwen3-ASR-0.6B",
    ],
    "faster-whisper": [
        "large-v3-turbo",
        "large-v3",
        "medium",
        "small",
        "base",
        "tiny",
    ],
}
_WEB_LANGUAGE_CHOICES = [
    ("Auto detect", "auto"),
    ("Chinese", "zh"),
    ("English", "en"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Cantonese", "yue"),
    ("French", "fr"),
    ("German", "de"),
    ("Spanish", "es"),
    ("Portuguese", "pt"),
    ("Italian", "it"),
    ("Russian", "ru"),
]
_WEB_CHINESE_OUTPUT_CHOICES = [
    ("Traditional Chinese (Taiwan)", "tw"),
    ("Traditional Chinese + Taiwan phrases", "twp"),
    ("Keep model output", "none"),
]


def _web_job_status(job: dict[str, Any]) -> str:
    status = str(job.get("status", "unknown")).replace("_", " ").title()
    stage = str(job.get("stage") or "").replace("_", " ")
    message = f"**Status:** {status}"
    if stage and stage.lower() != status.lower():
        message += f" — {stage}"
    if job.get("error"):
        message += f"\n\n{job['error']}"
    return message


def _cache_result_files(job_id: str, paths: list[str]) -> list[str]:
    """Copy exports into the temporary tree Gradio is allowed to serve."""
    cache_dir = Path(tempfile.gettempdir()) / "echoscript-web" / job_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached: list[str] = []
    for raw_path in paths:
        source = Path(raw_path)
        destination = cache_dir / source.name
        shutil.copy2(source, destination)
        cached.append(str(destination))
    return cached


def _refresh_web_job(
    controller: LocalJobController,
    completed_results: dict[str, tuple[str, list[str]]],
    job_id: str | None,
) -> tuple[str, str, list[str]]:
    """Return display-ready state without turning a routine refresh into a UI error."""
    if not job_id:
        return "**Status:** Ready", "", []
    try:
        job = controller.job(job_id)
    except KeyError:
        completed_results.pop(job_id, None)
        return "**Status:** Job not found. Submit it again.", "", []

    status = _web_job_status(job)
    if job.get("status") != "done":
        completed_results.pop(job_id, None)
        return status, "", []

    cached = completed_results.get(job_id)
    if cached is None:
        try:
            text, result_paths = controller.result(job_id)
            cached_paths = _cache_result_files(job_id, result_paths)
            for cached_path in cached_paths:
                if Path(cached_path).name == "result.txt":
                    Path(cached_path).write_text(text, encoding="utf-8")
                    break
            cached = (text, cached_paths)
        except (OSError, ValueError) as exc:
            return f"**Status:** Could not load result — {exc}", "", []
        completed_results[job_id] = cached
    text, paths = cached
    return status, text, paths


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

    def submit_upload(
        self,
        source: str | Path,
        options: JobOptions | dict[str, Any],
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser()
        if not source_path.is_file():
            raise ValueError("Uploaded media file does not exist")
        normalized = self._options(options)
        job = self.store.create_job(
            source_type="upload",
            source_value=source_path.name,
            media_path=None,
            options=normalized,
            initial_status="uploading",
        )
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
        from echoscript.worker.worker import _cleanup_expired_jobs

        _cleanup_expired_jobs(
            self.store,
            self.settings.jobs_dir,
            self.settings.job_retention_days,
        )
        self._last_cleanup = now

    def _supervise_once(self) -> None:
        if self._process is not None:
            return_code = self._process.poll()
            if return_code is None:
                return
            self._process.wait(timeout=0)
            self._process = None
            running = set(self.store.running_job_ids())
            if return_code != 0 and self._active_job_id is not None:
                running.add(self._active_job_id)
            if running:
                self._handle_worker_crash(running)
            elif self._active_job_id is not None:
                self._crash_retries.pop(self._active_job_id, None)
            self._active_job_id = None
        next_job_id = self.store.next_queued_job_id()
        if next_job_id is None:
            self._maybe_cleanup_expired_jobs()
            return
        self._active_job_id = next_job_id
        try:
            self._process = self._popen_factory(
                [sys.executable, "-m", "echoscript.cli", "worker"],
                start_new_session=True,
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
                recovered = self.store.recover_running_jobs()
                if recovered:
                    log.warning(
                        "requeued %s job(s) left running by a previous worker",
                        recovered,
                    )
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
    try:
        import gradio as gr
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Gradio is required by the default echoscript installation") from exc

    controller = controller or LocalJobController()
    completed_results: dict[str, tuple[str, list[str]]] = {}

    def model_dropdown(backend):
        choices = _WEB_MODEL_CHOICES[backend]
        return gr.Dropdown(choices=choices, value=choices[0], label="Model")

    def submit_job(file_path, url, backend, model, language, diarize, timestamps, zh_script):
        choices = _WEB_MODEL_CHOICES[backend]
        if model not in choices:
            model = choices[0]
        options = JobOptions(
            asr_backend=backend,
            asr_model=model,
            language=None if language in {"", "auto"} else language,
            diarize=bool(diarize),
            timestamps=bool(timestamps),
            zh_script=None if zh_script == "none" else zh_script,
        ).to_dict()
        if file_path:
            job = controller.submit_upload(Path(file_path), options)
        elif url and url.strip():
            job = controller.submit_url(url.strip(), options)
        else:
            raise gr.Error("Upload a media file or enter a public URL")
        return job["id"], _web_job_status(job), "", []

    def refresh_job(job_id):
        return _refresh_web_job(controller, completed_results, job_id)

    def load_latest_job():
        latest_job_id = controller.latest_job_id()
        status, text, files = _refresh_web_job(
            controller,
            completed_results,
            latest_job_id,
        )
        return latest_job_id, status, text, files

    with gr.Blocks(title="echoscript") as demo:
        gr.Markdown(
            "# echoscript\n"
            "Upload audio/video or submit a public URL. For login-required YouTube, "
            "download it locally with the CLI first so browser cookies stay on your computer."
        )
        with gr.Row():
            file_input = gr.File(label="Audio / video", type="filepath")
            url_input = gr.Textbox(label="Public URL", placeholder="https://www.youtube.com/watch?v=...")
        gr.Markdown(
            "For members-only or login-required YouTube videos, run this on the computer "
            "that has your signed-in browser:\n\n"
            "`echoscript download 'YOUTUBE_URL' --browser chrome --output-dir .`"
        )
        with gr.Row():
            backend = gr.Dropdown(["qwen", "faster-whisper"], value="qwen", label="ASR backend")
            model = gr.Dropdown(
                _WEB_MODEL_CHOICES["qwen"],
                value=_WEB_MODEL_CHOICES["qwen"][0],
                label="Model",
            )
            language = gr.Dropdown(
                _WEB_LANGUAGE_CHOICES,
                value="auto",
                label="Language",
                allow_custom_value=True,
                info="Choose a common language or enter another language code.",
            )
        with gr.Row():
            diarize = gr.Checkbox(value=False, label="Speaker diarization")
            timestamps = gr.Checkbox(value=True, label="Timestamps")
            zh_script = gr.Dropdown(
                _WEB_CHINESE_OUTPUT_CHOICES,
                value="tw",
                label="Chinese output",
            )
        submit_button = gr.Button("Submit", variant="primary")
        job_id = gr.State(value=None)
        job_status = gr.Markdown("**Status:** Ready")
        with gr.Row():
            with gr.Column(scale=2):
                result_text = gr.Textbox(
                    label="Transcript",
                    lines=16,
                    interactive=False,
                )
            with gr.Column(scale=1, min_width=260):
                result_files = gr.File(
                    label="Download results",
                    file_count="multiple",
                    interactive=False,
                    height=180,
                )
        refresh_timer = gr.Timer(4.0)

        backend.change(model_dropdown, inputs=[backend], outputs=[model])
        submit_button.click(
            submit_job,
            inputs=[file_input, url_input, backend, model, language, diarize, timestamps, zh_script],
            outputs=[job_id, job_status, result_text, result_files],
        )
        refresh_timer.tick(
            refresh_job,
            inputs=[job_id],
            outputs=[job_status, result_text, result_files],
        )
        demo.load(
            load_latest_job,
            outputs=[job_id, job_status, result_text, result_files],
        )
    return demo


def run_web() -> None:
    server_name = os.getenv("ECHOSCRIPT_WEB_HOST", "0.0.0.0")
    controller = LocalJobController()
    controller.start()
    try:
        demo = create_web_app(controller)
        demo.launch(
            server_name=server_name,
            server_port=int(os.getenv("ECHOSCRIPT_WEB_PORT", "7860")),
            footer_links=[],
        )
    finally:
        controller.stop()
