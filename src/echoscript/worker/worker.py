from __future__ import annotations

import fcntl
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import IO

from echoscript.config import Settings
from echoscript.pipeline.pipeline import TranscriptionPipeline
from echoscript.pipeline.checkpoints import job_lock
from echoscript.storage import JobStore
from echoscript.storage.jobs import JobCancelled
from echoscript.schema import JobOptions
from .model_manager import ModelManager


log = logging.getLogger("echoscript.worker")
def _acquire_worker_lock(path: str | Path) -> IO[str]:
    """Hold a non-blocking process lock for this worker's lifetime."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"Another echoscript worker holds {lock_path}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def _cleanup_expired_jobs(
    store: JobStore,
    jobs_dir: str | Path,
    retention_days: int,
    *,
    now: float | None = None,
) -> int:
    if retention_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - retention_days * 86_400
    root = Path(jobs_dir).resolve()
    deleted = 0
    for job_id in store.expired_terminal_job_ids(cutoff):
        job_path = (root / job_id).resolve()
        if job_path.parent != root:
            log.error("refusing to clean unsafe job path for id %r", job_id)
            continue
        with job_lock(job_path):
            try:
                current = store.get_job(job_id)
            except KeyError:
                continue
            # Resume and editing use the same stable lock. Selection alone is
            # stale evidence: the user may have resumed the job while we waited.
            if current["status"] not in {"done", "failed", "cancelled"} or current["updated_at"] >= cutoff:
                continue
            if job_path.exists():
                shutil.rmtree(job_path)
            # Remove this job's legacy Gradio export copies when its retention expires.
            legacy_root = Path(tempfile.gettempdir()) / "echoscript-web"
            legacy_path = (legacy_root / job_id).resolve()
            if legacy_path.parent == legacy_root.resolve() and legacy_path.is_dir():
                shutil.rmtree(legacy_path)
            if store.delete_expired_terminal_job(job_id, cutoff):
                deleted += 1
    return deleted


def run_worker(job_id: str | None = None, idle_timeout: float = 0) -> int:
    settings = Settings.from_env()
    settings.ensure_directories()
    try:
        worker_lock = _acquire_worker_lock(
            settings.worker_lock_path or settings.data_dir / "worker.lock"
        )
    except RuntimeError:
        return 75  # Another live worker owns the queue; this is not a job failure.
    try:
        return _run_locked_worker(settings, job_id, idle_timeout)
    finally:
        worker_lock.close()


def _run_locked_worker(settings: Settings, job_id: str | None, idle_timeout: float = 0) -> int:
    store = JobStore(settings.db_path)
    recovered = store.recover_running_jobs()
    if recovered:
        log.warning("requeued %s job(s) left running by a previous worker", recovered)
    if not store.has_queued_jobs():
        log.info("no queued jobs; worker exiting")
        return 0

    job = store.claim_next_job(job_id, worker_pid=os.getpid())
    if job is None:
        return 0

    models = ModelManager(hf_token=settings.hf_token)
    pipeline = TranscriptionPipeline(settings, models)
    log.info("worker started pid=%s", os.getpid())
    try:
        while job is not None:
            job_id = job["id"]

            def on_stage(stage: str) -> None:
                store.set_stage(job_id, stage)

            try:
                if settings.release_between_stages:
                    models.drop_diarizer()
                result_path = pipeline.run(job, on_stage=on_stage)
                store.complete(job_id, str(result_path))
                log.info("job %s completed", job_id)
            except JobCancelled:
                store.mark_cancelled(job_id)
                log.info("job %s cancelled after saving current stage", job_id)
            except Exception as exc:
                store.fail(job_id, f"{type(exc).__name__}: {exc}")
                log.exception("job %s failed", job_id)
                models.unload_all()
            if idle_timeout <= 0:
                break
            current_backend = JobOptions.from_dict(job["options"]).asr_backend
            deadline = time.monotonic() + idle_timeout
            while True:
                next_id = store.next_queued_job_id()
                if next_id:
                    next_options = JobOptions.from_dict(store.get_job(next_id)["options"])
                    if next_options.asr_backend != current_backend:
                        log.info("backend changed; restarting worker before claiming next job")
                        return 0
                job = store.claim_next_job(worker_pid=os.getpid())
                if job is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.info("model idle timeout reached; unloading and exiting worker")
                    return 0
                time.sleep(min(0.25, remaining))
    finally:
        models.unload_all()
    return 0
