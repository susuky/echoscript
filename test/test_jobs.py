import pytest

from echoscript.schema import JobOptions
from echoscript.storage import JobStore
from echoscript.worker.worker import _cleanup_expired_jobs


def test_job_lifecycle(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create_job(source_type="upload", source_value="a.mp4", media_path="/tmp/a.mp4", options=JobOptions())
    assert created["status"] == "queued"

    claimed = store.claim_next_job()
    assert claimed["id"] == created["id"]
    assert claimed["status"] == "running"

    store.set_stage(created["id"], "transcribing")
    store.complete(created["id"], "/tmp/result.json")
    done = store.get_job(created["id"])
    assert done["status"] == "done"
    assert done["result_path"] == "/tmp/result.json"


def test_recover_running_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(source_type="upload", source_value="a", media_path="/tmp/a", options=JobOptions())
    store.claim_next_job()
    assert store.recover_running_jobs() == 1
    assert store.get_job(job["id"])["status"] == "queued"


def test_expired_terminal_cleanup_never_selects_active_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    done = store.create_job(
        source_type="upload", source_value="done", media_path="/tmp/done", options=JobOptions()
    )
    failed = store.create_job(
        source_type="upload", source_value="failed", media_path="/tmp/failed", options=JobOptions()
    )
    active = store.create_job(
        source_type="upload", source_value="active", media_path="/tmp/active", options=JobOptions()
    )
    store.complete(done["id"], "/tmp/result")
    store.fail(failed["id"], "failed")
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET updated_at = 0")

    jobs_dir = tmp_path / "jobs"
    for job in (done, failed, active):
        (jobs_dir / job["id"]).mkdir(parents=True)

    assert _cleanup_expired_jobs(store, jobs_dir, 1, now=200_000) == 2
    with pytest.raises(KeyError):
        store.get_job(done["id"])
    with pytest.raises(KeyError):
        store.get_job(failed["id"])
    assert store.get_job(active["id"])["status"] == "queued"
    assert (jobs_dir / active["id"]).is_dir()


def test_cleanup_rejects_path_outside_jobs_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()

    class UnsafeStore:
        def expired_terminal_job_ids(self, _cutoff):
            return ["../outside"]

        def delete_expired_terminal_job(self, _job_id, _cutoff):
            raise AssertionError("unsafe row must not be deleted")

    assert _cleanup_expired_jobs(UnsafeStore(), tmp_path / "jobs", 1, now=200_000) == 0
    assert outside.is_dir()


def test_upload_is_not_claimable_until_finished(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(
        source_type="upload",
        source_value="recording.wav",
        media_path=None,
        options=JobOptions(),
        initial_status="uploading",
    )

    assert job["status"] == "uploading"
    assert store.claim_next_job() is None

    store.finish_upload(job["id"], "/tmp/recording.wav")
    claimed = store.claim_next_job()
    assert claimed["id"] == job["id"]
    assert claimed["media_path"] == "/tmp/recording.wav"


def test_upload_can_only_be_finished_once(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(
        source_type="upload",
        source_value="recording.wav",
        media_path=None,
        options=JobOptions(),
        initial_status="uploading",
    )

    store.finish_upload(job["id"], "/tmp/recording.wav")
    with pytest.raises(KeyError):
        store.finish_upload(job["id"], "/tmp/other.wav")


def test_worker_claims_requested_job_even_if_an_older_upload_just_finished(tmp_path):
    from echoscript.schema import JobOptions
    from echoscript.storage import JobStore
    store = JobStore(tmp_path/'jobs.sqlite3')
    upload = store.create_job(source_type='upload', source_value='old.wav', media_path=None,
                              options=JobOptions(), initial_status='uploading')
    queued = store.create_job(source_type='url', source_value='https://93.184.216.34/media',
                              media_path=None, options=JobOptions())
    assert store.next_queued_job_id() == queued['id']
    store.finish_upload(upload['id'], '/tmp/not-read.wav')
    assert store.claim_next_job(queued['id'])['id'] == queued['id']
    assert store.get_job(upload['id'])['status'] == 'queued'


def test_retention_removes_legacy_web_export_copy(tmp_path, monkeypatch):
    import sqlite3
    import time
    from echoscript.schema import JobOptions
    from echoscript.storage import JobStore
    from echoscript.worker.worker import _cleanup_expired_jobs
    store = JobStore(tmp_path/'jobs.sqlite3')
    job = store.create_job(source_type='upload', source_value='a.wav', media_path=None, options=JobOptions())
    store.fail(job['id'], 'failed')
    with sqlite3.connect(store.db_path) as conn:
        conn.execute('UPDATE jobs SET updated_at=? WHERE id=?', (time.time()-86400*31, job['id']))
    monkeypatch.setattr('echoscript.worker.worker.tempfile.gettempdir', lambda: str(tmp_path/'temp'))
    cached = tmp_path/'temp'/'echoscript-web'/job['id']
    cached.mkdir(parents=True)
    (cached/'result.txt').write_text('old transcript')
    assert _cleanup_expired_jobs(store, tmp_path/'jobs', 30) == 1
    assert not cached.exists()
