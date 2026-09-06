from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from echoscript.schema import JobOptions


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_value TEXT,
    media_path TEXT,
    audio_path TEXT,
    result_path TEXT,
    error TEXT,
    options_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    worker_pid INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
ON jobs(status, created_at);

"""


class JobCancelled(Exception):
    pass


class JobStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            if "worker_pid" not in {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN worker_pid INTEGER")
            if "cancel_requested" not in {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    def create_job(
        self,
        *,
        source_type: str,
        source_value: str | None,
        media_path: str | None,
        options: JobOptions,
        initial_status: str = "queued",
    ) -> dict[str, Any]:
        if initial_status not in {"queued", "uploading"}:
            raise ValueError(f"Unsupported initial job status: {initial_status}")
        job_id = uuid.uuid4().hex
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, status, stage, source_type, source_value, media_path,
                    options_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    initial_status,
                    initial_status,
                    source_type,
                    source_value,
                    media_path,
                    json.dumps(options.to_dict()),
                    now,
                    now,
                ),
            )
        return self.get_job(job_id)

    def finish_upload(self, job_id: str, media_path: str) -> None:
        """Atomically make a fully written upload visible to workers."""
        with closing(self._connect()) as conn:
            changed = conn.execute(
                """
                UPDATE jobs
                SET status='queued', stage='queued', media_path=?, updated_at=?
                WHERE id=? AND status='uploading'
                """,
                (media_path, time.time(), job_id),
            ).rowcount
        if changed != 1:
            raise KeyError(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._decode(row)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._decode(row) for row in rows]

    def has_queued_jobs(self) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE status = 'queued' LIMIT 1"
            ).fetchone()
        return row is not None

    def next_queued_job_id(self) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
        return str(row["id"]) if row is not None else None

    def running_job_ids(self, worker_pid: int | None = None) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = 'running' "
                + ("AND worker_pid=? " if worker_pid is not None else "")
                + "ORDER BY created_at ASC",
                (worker_pid,) if worker_pid is not None else (),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def requeue_running_job(self, job_id: str, *, stage: str = "worker_crashed") -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute(
                """
                UPDATE jobs
                SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,
                    stage=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE ? END, updated_at=?
                WHERE id=? AND status='running'
                """,
                (stage, time.time(), job_id),
            ).rowcount
        return changed == 1

    def fail_active_job(self, job_id: str, error: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute(
                """
                UPDATE jobs
                SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'failed' END,
                    stage=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'failed' END,
                    error=?, updated_at=?
                WHERE id=? AND status IN ('queued', 'running')
                """,
                (error[:8000], time.time(), job_id),
            ).rowcount
        return changed == 1

    def stale_uploading_job_ids(self, cutoff: float, limit: int = 100) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status='uploading' AND updated_at < ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (cutoff, max(1, min(limit, 1000))),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def fail_stale_upload(self, job_id: str, cutoff: float, error: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute(
                """
                UPDATE jobs
                SET status='failed', stage='failed', error=?, updated_at=?
                WHERE id=? AND status='uploading' AND updated_at < ?
                """,
                (error[:8000], time.time(), job_id, cutoff),
            ).rowcount
        return changed == 1

    def expired_terminal_job_ids(self, cutoff: float, limit: int = 100) -> list[str]:
        """List terminal jobs older than cutoff for bounded cleanup."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status IN ('done', 'failed', 'cancelled') AND updated_at < ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (cutoff, max(1, min(limit, 1000))),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def delete_expired_terminal_job(self, job_id: str, cutoff: float) -> bool:
        """Conditionally delete one still-terminal expired row."""
        with closing(self._connect()) as conn:
            changed = conn.execute(
                """
                DELETE FROM jobs
                WHERE id = ? AND status IN ('done', 'failed', 'cancelled') AND updated_at < ?
                """,
                (job_id, cutoff),
            ).rowcount
        return changed == 1

    def recover_running_jobs(self) -> int:
        """Requeue jobs left running by a crashed/restarted single worker."""
        with closing(self._connect()) as conn:
            conn.execute("UPDATE jobs SET status='cancelled', stage='cancelled', updated_at=? WHERE status='running' AND cancel_requested=1", (time.time(),))
            changed = conn.execute(
                "UPDATE jobs SET status='queued', stage='recovered', updated_at=? WHERE status='running'",
                (time.time(),),
            ).rowcount
        return int(changed)

    def claim_next_job(self, job_id: str | None = None, *, worker_pid: int | None = None) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' "
                + ("AND id=? " if job_id is not None else "")
                + "ORDER BY created_at ASC LIMIT 1",
                (job_id,) if job_id is not None else (),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            now = time.time()
            changed = conn.execute(
                """
                UPDATE jobs SET status='running', stage='starting', updated_at=?, worker_pid=?
                WHERE id=? AND status='queued'
                """,
                (now, worker_pid, row["id"]),
            ).rowcount
            if changed != 1:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
            return self.get_job(row["id"])
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def set_stage(self, job_id: str, stage: str, **paths: str | None) -> None:
        if self.get_job(job_id).get("cancel_requested"):
            raise JobCancelled()
        values: dict[str, Any] = {"stage": stage}
        values.update(paths)
        self._update(job_id, **values)

    def complete(self, job_id: str, result_path: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'done' END, stage=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'done' END, result_path=?, error=NULL, updated_at=? WHERE id=?", (result_path, time.time(), job_id))

    def cancel(self, job_id: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute("UPDATE jobs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, stage=CASE WHEN status='queued' THEN 'cancelled' ELSE stage END, updated_at=? WHERE id=? AND status IN ('queued','running')", (time.time(), job_id)).rowcount
        return changed == 1

    def mark_cancelled(self, job_id: str) -> None:
        self._update(job_id, status="cancelled", stage="cancelled")

    def set_result_path(self, job_id: str, result_path: str) -> None:
        """Record a reviewed revision without changing execution/cancellation state."""
        self._update(job_id, result_path=result_path)

    def resume(self, job_id: str) -> bool:
        with closing(self._connect()) as conn:
            changed = conn.execute("UPDATE jobs SET status='queued', stage='queued', cancel_requested=0, error=NULL, updated_at=? WHERE id=? AND status IN ('failed','cancelled','done')", (time.time(), job_id)).rowcount
        return changed == 1

    def fail(self, job_id: str, error: str) -> None:
        with closing(self._connect()) as conn:
            changed = conn.execute(
                "UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'failed' END, "
                "stage=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'failed' END, "
                "error=?, updated_at=? WHERE id=?", (error[:8000], time.time(), job_id),
            ).rowcount
        if changed != 1:
            raise KeyError(job_id)

    def _update(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "stage", "media_path", "audio_path", "result_path", "error"}
        values = {k: v for k, v in values.items() if k in allowed}
        if not values:
            return
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in values)
        args = [*values.values(), job_id]
        with closing(self._connect()) as conn:
            changed = conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", args).rowcount
        if changed != 1:
            raise KeyError(job_id)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["options"] = json.loads(data.pop("options_json"))
        return data
