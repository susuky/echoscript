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


@pytest.mark.parametrize('terminal_action', ['requeue_running_job', 'fail_active_job', 'fail'])
def test_cancellation_intent_survives_a_concurrent_worker_failure(tmp_path, terminal_action):
    store = JobStore(tmp_path / 'jobs.sqlite3')
    job = store.create_job(source_type='upload', source_value='a.wav', media_path='/tmp/a.wav', options=JobOptions())
    store.claim_next_job()
    assert store.cancel(job['id'])
    action = getattr(store, terminal_action)
    if terminal_action == 'requeue_running_job':
        action(job['id'])
    else:
        action(job['id'], 'worker failed while stopping')
    stopped = store.get_job(job['id'])
    assert stopped['status'] == stopped['stage'] == 'cancelled'
    assert store.claim_next_job() is None
    assert store.resume(job['id'])
    assert not store.get_job(job['id'])['cancel_requested']


def test_cancelled_jobs_follow_the_same_retention_policy(tmp_path):
    store = JobStore(tmp_path / 'jobs.sqlite3')
    job = store.create_job(source_type='upload', source_value='a.wav', media_path='/tmp/a.wav', options=JobOptions())
    assert store.cancel(job['id'])
    with store._connect() as conn:
        conn.execute('UPDATE jobs SET updated_at=0 WHERE id=?', (job['id'],))
    jobs_dir = tmp_path / 'jobs'
    directory = jobs_dir / job['id']
    directory.mkdir(parents=True)
    (directory / 'checkpoint.json').write_text('{}')
    assert _cleanup_expired_jobs(store, jobs_dir, 1, now=200_000) == 1
    assert not directory.exists()
    with pytest.raises(KeyError):
        store.get_job(job['id'])


def test_cleanup_rechecks_after_resume_wins_the_job_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from echoscript.pipeline.checkpoints import job_lock

    store = JobStore(tmp_path / 'jobs.sqlite3')
    job = store.create_job(source_type='upload', source_value='a.wav', media_path='/tmp/a.wav', options=JobOptions())
    store.complete(job['id'], '/tmp/result.json')
    with store._connect() as conn:
        conn.execute('UPDATE jobs SET updated_at=0 WHERE id=?', (job['id'],))
    jobs_dir = tmp_path / 'jobs'
    directory = jobs_dir / job['id']
    directory.mkdir(parents=True)
    audio = directory / 'audio.wav'
    audio.write_bytes(b'saved audio')

    @contextmanager
    def resume_before_cleanup_acquires_lock(path):
        with job_lock(path):
            assert store.resume(job['id'])
        with job_lock(path):
            yield

    monkeypatch.setattr('echoscript.worker.worker.job_lock', resume_before_cleanup_acquires_lock)
    assert _cleanup_expired_jobs(store, jobs_dir, 1, now=200_000) == 0
    assert store.get_job(job['id'])['status'] == 'queued'
    assert audio.read_bytes() == b'saved audio'
    assert (jobs_dir / '.locks' / job['id']).is_file()
