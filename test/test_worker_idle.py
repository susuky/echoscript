import os
from unittest.mock import MagicMock, patch

import pytest

from echoscript.config import Settings
from echoscript.schema import JobOptions
from echoscript.storage import JobStore
from echoscript.web import LocalJobController
from echoscript.worker.worker import _run_locked_worker


def test_worker_reuses_models_and_resets_idle_deadline_for_a_later_job(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.sqlite3', jobs_dir=tmp_path/'jobs')
    store = JobStore(settings.db_path)
    first = store.create_job(source_type='upload', source_value='one.wav', media_path=None, options=JobOptions())
    clock = [0.0]
    processed = []
    later = []
    models = MagicMock()

    def sleep(seconds):
        clock[0] += seconds
        if not later:
            later.append(store.create_job(source_type='upload', source_value='two.wav', media_path=None, options=JobOptions()))

    def transcribe(job, **kwargs):
        models.unload_all.assert_not_called()
        assert job['worker_pid'] == os.getpid()
        processed.append(job['id'])
        kwargs['on_stage']('transcribing')
        return tmp_path/'result.json'

    with (patch('echoscript.worker.worker.ModelManager', return_value=models) as factory,
          patch('echoscript.worker.worker.TranscriptionPipeline') as pipeline,
          patch('echoscript.worker.worker.time.monotonic', side_effect=lambda: clock[0]),
          patch('echoscript.worker.worker.time.sleep', side_effect=sleep)):
        pipeline.return_value.run.side_effect = transcribe
        assert _run_locked_worker(settings, first['id'], idle_timeout=0.5) == 0
    assert processed == [first['id'], later[0]['id']]
    assert clock[0] == 0.75
    factory.assert_called_once()
    models.unload_all.assert_called_once()
    assert all(store.get_job(job)['status'] == 'done' for job in processed)


def test_zero_idle_timeout_exits_after_one_job(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.sqlite3', jobs_dir=tmp_path/'jobs')
    store = JobStore(settings.db_path)
    jobs = [store.create_job(source_type='upload', source_value='a.wav', media_path=None, options=JobOptions()) for _ in range(2)]
    with (patch('echoscript.worker.worker.ModelManager') as models,
          patch('echoscript.worker.worker.TranscriptionPipeline') as pipeline):
        pipeline.return_value.run.return_value = tmp_path/'result.json'
        assert _run_locked_worker(settings, jobs[0]['id'], idle_timeout=0) == 0
    assert store.get_job(jobs[0]['id'])['status'] == 'done'
    assert store.get_job(jobs[1]['id'])['status'] == 'queued'
    models.return_value.unload_all.assert_called_once()


def test_crash_recovery_targets_later_job_owned_by_warm_worker(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.sqlite3', jobs_dir=tmp_path/'jobs')
    controller = LocalJobController(settings)
    first, later = [controller.store.create_job(source_type='upload', source_value='a.wav', media_path=None, options=JobOptions()) for _ in range(2)]
    controller.store.complete(first['id'], str(tmp_path/'result.json'))
    controller.store.claim_next_job(later['id'], worker_pid=456)
    controller._active_job_id = first['id']
    controller._process = MagicMock(pid=456)
    controller._process.poll.return_value = -9
    with patch.object(controller, '_recover_if_worker_idle', return_value=False):
        controller._supervise_once()
    assert controller.store.get_job(first['id'])['status'] == 'done'
    assert controller.store.get_job(later['id'])['status'] == 'queued'
    assert controller._crash_retries == {later['id']: 1}


@pytest.mark.parametrize('value', ['-1', 'nan', 'inf'])
def test_idle_timeout_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv('ECHOSCRIPT_MODEL_IDLE_TIMEOUT_SECONDS', value)
    with pytest.raises(ValueError, match='finite and nonnegative'):
        Settings.from_env()


def test_existing_job_database_is_migrated_without_losing_jobs(tmp_path):
    import sqlite3
    from echoscript.storage.jobs import SCHEMA
    db_path = tmp_path/'jobs.sqlite3'
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA.replace(',\n    worker_pid INTEGER', ''))
        conn.execute("INSERT INTO jobs (id,status,stage,source_type,options_json,created_at,updated_at) VALUES ('legacy','queued','queued','upload','{}',1,1)")
    store = JobStore(db_path)
    assert store.get_job('legacy')['status'] == 'queued'
    assert store.claim_next_job('legacy', worker_pid=123)['worker_pid'] == 123
    assert store.running_job_ids(worker_pid=123) == ['legacy']
    assert store.running_job_ids(worker_pid=456) == []


def test_backend_change_leaves_next_job_for_fresh_worker(tmp_path):
    from echoscript.schema import JobOptions
    from echoscript.storage import JobStore
    from echoscript.config import Settings
    from echoscript.worker.worker import _run_locked_worker
    from unittest.mock import patch
    settings = Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.db', jobs_dir=tmp_path/'jobs')
    store = JobStore(settings.db_path)
    first = store.create_job(source_type='upload', source_value='one', media_path=None, options=JobOptions())
    next_job = store.create_job(source_type='upload', source_value='two', media_path=None,
                                options=JobOptions(asr_backend='faster-whisper',asr_model='large-v3'))
    with patch('echoscript.worker.worker.ModelManager'), patch('echoscript.worker.worker.TranscriptionPipeline') as pipeline:
        pipeline.return_value.run.return_value = tmp_path/'result.json'
        assert _run_locked_worker(settings, first['id'], idle_timeout=0.2) == 0
        assert pipeline.return_value.run.call_count == 1
    assert store.get_job(first['id'])['status'] == 'done'
    assert store.get_job(next_job['id'])['status'] == 'queued'
    assert store.get_job(next_job['id'])['worker_pid'] is None
