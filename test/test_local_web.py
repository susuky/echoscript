import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from echoscript.config import Settings
from echoscript.schema import JobOptions
from echoscript.storage import JobStore
from echoscript.web import LocalJobController
from echoscript.worker.worker import run_worker


def settings_for(tmp_path, **overrides):
    values = {
        "data_dir": tmp_path,
        "db_path": tmp_path / "jobs.sqlite3",
        "jobs_dir": tmp_path / "jobs",
        "worker_lock_path": tmp_path / "worker.lock",
    }
    values.update(overrides)
    return Settings(**values)


def test_local_upload_is_copied_and_queued(tmp_path):
    source = tmp_path / "recording.m4a"
    source.write_bytes(b"audio")
    controller = LocalJobController(settings_for(tmp_path, max_upload_bytes=10))

    job = controller.submit_upload(source, JobOptions())

    stored = controller.store.get_job(job["id"])
    destination = tmp_path / "jobs" / job["id"] / "upload.m4a"
    assert job["status"] == "queued"
    assert "media_path" not in job
    assert stored["media_path"] == str(destination)
    assert destination.read_bytes() == b"audio"
    source.unlink()
    assert destination.read_bytes() == b"audio"
    assert list(destination.parent.glob("*.part")) == []


def test_local_upload_is_atomically_published_before_queue(tmp_path):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"audio")
    controller = LocalJobController(settings_for(tmp_path))
    finish_upload = controller.store.finish_upload

    def verify_then_finish(job_id, media_path):
        destination = tmp_path / "jobs" / job_id / "upload.wav"
        assert destination == Path(media_path)
        assert destination.read_bytes() == b"audio"
        assert not (destination.parent / "upload.wav.part").exists()
        finish_upload(job_id, media_path)

    with patch.object(controller.store, "finish_upload", side_effect=verify_then_finish):
        controller.submit_upload(source, JobOptions())


def test_local_upload_over_limit_is_failed_and_removed(tmp_path):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"too large")
    controller = LocalJobController(settings_for(tmp_path, max_upload_bytes=3))

    with pytest.raises(ValueError, match="size limit"):
        controller.submit_upload(source, JobOptions())

    [job] = controller.store.list_jobs()
    assert job["status"] == "failed"
    assert not (tmp_path / "jobs" / job["id"]).exists()
    assert controller.store.has_queued_jobs() is False


def test_local_url_and_diarization_preflight(tmp_path):
    enabled = LocalJobController(settings_for(tmp_path))
    with pytest.raises(ValueError, match="HF_TOKEN"):
        enabled.submit_url(
            "https://93.184.216.34/video",
            JobOptions(diarize=True),
        )

    job = enabled.submit_url("https://93.184.216.34/video", JobOptions())
    assert job["status"] == "queued"


def test_local_result_reads_job_output_directly(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    job = controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    output = tmp_path / "jobs" / job["id"] / "output"
    output.mkdir(parents=True)
    (output / "result.txt").write_text("hello", encoding="utf-8")
    (output / "result.json").write_text('{"text":"hello"}', encoding="utf-8")
    controller.store.complete(job["id"], str(output / "result.json"))

    text, paths = controller.result(job["id"])

    assert text == "hello"
    assert paths == [
        str(output / "result.txt"),
        str(output / "result.json"),
    ]


def test_latest_job_id_returns_most_recent_job(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    first = controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/first",
        media_path=None,
        options=JobOptions(),
    )
    second = controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/second",
        media_path=None,
        options=JobOptions(),
    )

    assert first["id"] != second["id"]
    assert controller.latest_job_id() == second["id"]


def test_local_result_falls_back_to_canonical_json_text(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    job = controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    output = tmp_path / "jobs" / job["id"] / "output"
    output.mkdir(parents=True)
    (output / "result.json").write_text('{"text":"from json"}', encoding="utf-8")
    controller.store.complete(job["id"], str(output / "result.json"))

    text, paths = controller.result(job["id"])

    assert text == "from json"
    assert paths == [str(output / "result.json")]


def test_local_result_renders_speakers_from_canonical_json(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    job = controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(diarize=True),
    )
    output = tmp_path / "jobs" / job["id"] / "output"
    output.mkdir(parents=True)
    (output / "result.txt").write_text("hello goodbye", encoding="utf-8")
    (output / "result.json").write_text(
        '{"text":"hello goodbye","speakers":["SPEAKER_00","SPEAKER_01"],'
        '"segments":[{"start":0,"end":1,"text":"hello","speaker":"SPEAKER_00",'
        '"words":[]},{"start":1,"end":2,"text":"goodbye","speaker":"SPEAKER_01",'
        '"words":[]}]}',
        encoding="utf-8",
    )
    controller.store.complete(job["id"], str(output / "result.json"))

    text, _ = controller.result(job["id"])

    assert text == "[Speaker 1] hello\n\n[Speaker 2] goodbye\n"


def test_supervisor_only_spawns_for_queue_and_never_overlaps(tmp_path):
    process = MagicMock(pid=123)
    process.poll.return_value = None
    popen = MagicMock(return_value=process)
    controller = LocalJobController(settings_for(tmp_path), popen_factory=popen)

    controller._supervise_once()
    popen.assert_not_called()

    controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    controller._supervise_once()
    popen.assert_called_once()
    command = popen.call_args.args[0]
    assert command[1:4] == ["-m", "echoscript.cli", "worker"]
    assert command[-2] == "--job-id"
    assert command[-1] == controller.store.next_queued_job_id()
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["env"]["ECHOSCRIPT_DB_PATH"] == str(controller.settings.db_path)

    controller._supervise_once()
    popen.assert_called_once()

    process.poll.return_value = 0
    controller._supervise_once()
    assert popen.call_count == 2


def test_start_recovers_running_jobs_before_thread(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    job = controller.store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    controller.store.claim_next_job()

    with patch.object(controller, "_supervisor_loop"):
        controller.start()
    try:
        assert controller.store.get_job(job["id"])["status"] == "queued"
    finally:
        controller.stop()


def test_second_controller_for_same_data_dir_is_rejected(tmp_path):
    first = LocalJobController(settings_for(tmp_path))
    second = LocalJobController(settings_for(tmp_path))
    with patch.object(first, "_supervisor_loop"):
        first.start()
    try:
        with patch.object(second, "_supervisor_loop"):
            with pytest.raises(RuntimeError, match="dispatcher"):
                second.start()
    finally:
        first.stop()


def test_stop_terminates_and_reaps_active_process_group(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    process = MagicMock(pid=123)
    process.pid = 123
    process.poll.return_value = None
    process.wait.side_effect = [
        subprocess.TimeoutExpired(["worker"], 5),
        0,
    ]
    controller._process = process

    with (
        patch("echoscript.web.os.getpgid", return_value=456),
        patch("echoscript.web.os.killpg") as killpg,
    ):
        controller.stop()

    assert killpg.call_args_list[0].args == (456, signal.SIGTERM)
    assert killpg.call_args_list[1].args == (456, signal.SIGKILL)
    assert process.wait.call_args_list[0].kwargs == {"timeout": 5}
    assert process.wait.call_args_list[1].kwargs == {"timeout": 5}
    assert controller._process is None


def test_two_queued_jobs_spawn_serial_workers(tmp_path):
    settings = settings_for(tmp_path)
    store = JobStore(settings.db_path)
    job_ids = [
        store.create_job(
            source_type="url",
            source_value=f"https://93.184.216.34/video/{index}",
            media_path=None,
            options=JobOptions(),
        )["id"]
        for index in range(2)
    ]
    processes = []

    def spawn(*_args, **_kwargs):
        claimed = store.claim_next_job()
        process = MagicMock(pid=123)
        process.poll.return_value = None
        process.job_id = claimed["id"]
        processes.append(process)
        return process

    controller = LocalJobController(settings, store=store, popen_factory=spawn)
    controller._supervise_once()
    assert len(processes) == 1
    controller._supervise_once()
    assert len(processes) == 1

    store.complete(processes[0].job_id, "/tmp/result-1.json")
    processes[0].poll.return_value = 0
    controller._supervise_once()
    assert len(processes) == 2
    assert [process.job_id for process in processes] == job_ids


def test_abnormal_child_after_claim_requeues_then_fails_after_three_crashes(tmp_path):
    settings = settings_for(tmp_path)
    store = JobStore(settings.db_path)
    job = store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    store.claim_next_job()

    crashed_processes = []

    def spawn(*_args, **_kwargs):
        process = MagicMock(pid=123)
        process.poll.return_value = 9
        crashed_processes.append(process)
        return process

    controller = LocalJobController(settings, store=store, popen_factory=spawn)
    initial = MagicMock(pid=123)
    initial.poll.return_value = 9
    controller._process = initial
    controller._active_job_id = job["id"]

    controller._supervise_once()
    retried = store.get_job(job["id"])
    assert retried["status"] == "queued"
    assert retried["stage"] == "worker_crashed_retry_1"

    controller._supervise_once()
    controller._supervise_once()
    failed = store.get_job(job["id"])
    assert failed["status"] == "failed"
    assert "3 times" in failed["error"]
    assert store.has_queued_jobs() is False


def test_normal_child_exit_does_not_bulk_recover(tmp_path):
    settings = settings_for(tmp_path)
    store = JobStore(settings.db_path)
    job = store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    store.complete(job["id"], "/tmp/result.json")
    process = MagicMock(pid=123)
    process.poll.return_value = 0
    controller = LocalJobController(settings, store=store)
    controller._process = process
    controller._active_job_id = job["id"]

    with patch.object(store, "recover_running_jobs", wraps=store.recover_running_jobs) as recover:
        controller._supervise_once()

    recover.assert_not_called()
    assert store.get_job(job["id"])["status"] == "done"


def test_idle_supervisor_cleans_expired_terminal_jobs(tmp_path):
    settings = settings_for(tmp_path, job_retention_days=1)
    store = JobStore(settings.db_path)
    job = store.create_job(
        source_type="url",
        source_value="https://93.184.216.34/video",
        media_path=None,
        options=JobOptions(),
    )
    job_dir = settings.jobs_dir / job["id"]
    job_dir.mkdir(parents=True)
    (job_dir / "artifact").write_text("old", encoding="utf-8")
    store.fail(job["id"], "failed")
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            "UPDATE jobs SET updated_at=? WHERE id=?",
            (time.time() - 2 * 86_400, job["id"]),
        )

    controller = LocalJobController(settings, store=store)
    controller._supervise_once()

    with pytest.raises(KeyError):
        store.get_job(job["id"])
    assert not job_dir.exists()


def test_start_fails_only_stale_uploads_and_removes_their_job_dirs(tmp_path):
    settings = settings_for(tmp_path)
    store = JobStore(settings.db_path)
    old = store.create_job(
        source_type="upload",
        source_value="old.wav",
        media_path=None,
        options=JobOptions(),
        initial_status="uploading",
    )
    fresh = store.create_job(
        source_type="upload",
        source_value="fresh.wav",
        media_path=None,
        options=JobOptions(),
        initial_status="uploading",
    )
    for job in (old, fresh):
        job_dir = settings.jobs_dir / job["id"]
        job_dir.mkdir(parents=True)
        (job_dir / "upload.wav.part").write_bytes(b"partial")
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            "UPDATE jobs SET updated_at=? WHERE id=?",
            (time.time() - 7200, old["id"]),
        )

    controller = LocalJobController(settings, store=store)
    with patch.object(controller, "_supervisor_loop"):
        controller.start()
    try:
        assert store.get_job(old["id"])["status"] == "failed"
        assert not (settings.jobs_dir / old["id"]).exists()
        assert store.get_job(fresh["id"])["status"] == "uploading"
        assert (settings.jobs_dir / fresh["id"] / "upload.wav.part").exists()
    finally:
        controller.stop()


def test_worker_exits_before_model_setup_when_queue_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ECHOSCRIPT_DB_PATH", raising=False)
    monkeypatch.delenv("ECHOSCRIPT_JOBS_DIR", raising=False)
    monkeypatch.delenv("ECHOSCRIPT_WORKER_LOCK_PATH", raising=False)

    with patch("echoscript.worker.worker.ModelManager") as models:
        assert run_worker() == 0

    models.assert_not_called()
    assert JobStore(tmp_path / "jobs.sqlite3").has_queued_jobs() is False


def test_live_worker_is_not_recovered_or_counted_as_crashed(tmp_path):
    from echoscript.worker.worker import _acquire_worker_lock
    controller = LocalJobController(settings_for(tmp_path), popen_factory=MagicMock())
    job = controller.store.create_job(source_type='url', source_value='https://93.184.216.34/media',
                                      media_path=None, options=JobOptions())
    controller.store.claim_next_job(job['id'])
    lock = _acquire_worker_lock(tmp_path/'worker.lock')
    try:
        with patch.object(controller, '_supervisor_loop'):
            controller.start()
        controller._supervise_once()
        assert controller.job(job['id'])['status'] == 'running'
        controller._popen_factory.assert_not_called()
        assert controller._crash_retries == {}
    finally:
        controller.stop()
        lock.close()


def test_stale_upload_is_cleaned_after_startup(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    job = controller.begin_upload('interrupted.wav', JobOptions())
    directory = tmp_path/'jobs'/job['id']
    directory.mkdir(parents=True)
    (directory/'upload.wav.part').write_bytes(b'partial')
    with patch.object(controller, '_supervisor_loop'):
        controller.start()
    try:
        assert controller.job(job['id'])['status'] == 'uploading'
        with sqlite3.connect(controller.settings.db_path) as conn:
            conn.execute('UPDATE jobs SET updated_at=? WHERE id=?', (time.time()-7200, job['id']))
        controller._last_cleanup -= 3601
        controller._supervise_once()
        assert controller.job(job['id'])['status'] == 'failed'
        assert not directory.exists()
    finally:
        controller.stop()


def test_busy_child_does_not_consume_job_retry_budget(tmp_path):
    controller = LocalJobController(settings_for(tmp_path))
    job = controller.store.create_job(source_type='url', source_value='https://93.184.216.34/media',
                                      media_path=None, options=JobOptions())
    controller.store.claim_next_job(job['id'])
    child = MagicMock()
    child.poll.return_value = 75
    controller._process = child
    controller._active_job_id = job['id']
    with patch.object(controller, '_recover_if_worker_idle', return_value=False):
        controller._supervise_once()
    assert controller._crash_retries == {}
    assert controller.job(job['id'])['status'] == 'running'
