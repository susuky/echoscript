from __future__ import annotations

import fcntl
import logging
import os
import shutil
import time
from pathlib import Path
from typing import IO

from echoscript.config import Settings
from echoscript.pipeline.pipeline import TranscriptionPipeline
from echoscript.storage import JobStore
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
        if job_path.exists():
            shutil.rmtree(job_path)
        if store.delete_expired_terminal_job(job_id, cutoff):
            deleted += 1
    return deleted


def run_worker() -> int:
    settings = Settings.from_env()
    settings.ensure_directories()
    worker_lock = _acquire_worker_lock(
        settings.worker_lock_path or settings.data_dir / "worker.lock"
    )
    store = JobStore(settings.db_path)
    recovered = store.recover_running_jobs()
    if recovered:
        log.warning("requeued %s job(s) left running by a previous worker", recovered)
    if not store.has_queued_jobs():
        log.info("no queued jobs; worker exiting")
        return 0

    models = ModelManager(hf_token=settings.hf_token)
    pipeline = TranscriptionPipeline(settings, models)
    log.info("worker started pid=%s", os.getpid())
    job = store.claim_next_job()
    if job is None:
        return 0

    job_id = job["id"]

    def on_stage(stage: str) -> None:
        store.set_stage(job_id, stage)

    try:
        result_path = pipeline.run(job, on_stage=on_stage)
        store.complete(job_id, str(result_path))
        log.info("job %s completed", job_id)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        store.fail(job_id, message)
        log.exception("job %s failed", job_id)
    finally:
        models.unload_all()
    return 0
