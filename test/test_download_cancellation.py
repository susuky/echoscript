import os
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, call, patch

import pytest

from echoscript.config import Settings
from echoscript.schema import JobOptions
from echoscript.web import LocalJobController
from echoscript.worker.worker import _acquire_worker_lock


def controller_for(tmp_path, **kwargs):
    return LocalJobController(Settings(
        data_dir=tmp_path, db_path=tmp_path / 'jobs.sqlite3',
        jobs_dir=tmp_path / 'jobs', worker_lock_path=tmp_path / 'worker.lock',
    ), **kwargs)


def queued_job(controller, source_type='url'):
    return controller.store.create_job(
        source_type=source_type, source_value='https://example.com/audio.wav',
        media_path=None, options=JobOptions(),
    )


@pytest.mark.parametrize('source_type,stage,cancelled,worker_pid', [
    ('url', 'ingesting', False, 123),
    ('url', 'transcribing', True, 123),
    ('url', 'aligning', True, 123),
    ('upload', 'ingesting', True, 123),
    ('url', 'ingesting', True, 456),
])
def test_supervisor_preserves_other_stages_and_workers(tmp_path, source_type, stage, cancelled, worker_pid):
    controller = controller_for(tmp_path)
    job = queued_job(controller, source_type)
    controller.store.claim_next_job(job['id'], worker_pid=worker_pid)
    controller.store.set_stage(job['id'], stage)
    if cancelled:
        controller.store.cancel(job['id'])
    process = MagicMock(pid=123)
    process.poll.return_value = None
    controller._process = process
    with patch('echoscript.web.os.killpg') as killpg:
        controller._supervise_once()
    killpg.assert_not_called()
    process.wait.assert_not_called()
    assert controller.store.get_job(job['id'])['status'] == 'running'


def test_cancelled_download_escalates_to_sigkill_for_the_process_group(tmp_path):
    controller = controller_for(tmp_path)
    job = queued_job(controller)
    controller.store.claim_next_job(job['id'], worker_pid=123)
    controller.store.set_stage(job['id'], 'ingesting')
    controller.store.cancel(job['id'])
    process = MagicMock(pid=123)
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired(['worker'], 5), 0]
    controller._process = process
    with (patch('echoscript.web.os.getpgid', return_value=456),
          patch('echoscript.web.os.killpg') as killpg):
        controller._supervise_once()
    assert killpg.call_args_list == [call(456, signal.SIGTERM), call(456, signal.SIGKILL)]
    assert process.wait.call_args_list == [call(timeout=5), call(timeout=5)]
    process.poll.return_value = -signal.SIGKILL
    process.wait.side_effect = None
    controller._supervise_once()
    assert controller.store.get_job(job['id'])['status'] == 'cancelled'


_TRICKLE_WORKER = r'''
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import echoscript.pipeline.pipeline as pipeline
from echoscript.worker.worker import run_worker

marker = Path(sys.argv[2])
if sys.argv[3] == '1':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

def download(*args, **kwargs):
    incoming, outgoing = socket.socketpair()
    def trickle():
        for index in range(10000):
            outgoing.sendall(b'x')
            if index == 2:
                marker.touch()
            time.sleep(0.02)
    threading.Thread(target=trickle, daemon=True).start()
    incoming.makefile('rb').read(1024 * 1024)

original_run = pipeline.TranscriptionPipeline.run
def run(self, job, *, on_stage=None):
    if job['source_type'] == 'url':
        return original_run(self, job, on_stage=on_stage)
    on_stage('transcribing')
    output = self.settings.jobs_dir / job['id'] / 'result.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('{"text":"queue continued"}')
    return output

pipeline.download_public_url = download
pipeline.TranscriptionPipeline.run = run
raise SystemExit(run_worker(sys.argv[1], idle_timeout=0.2))
'''


def wait_for(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, 'Worker did not reach the expected state'
        time.sleep(0.02)


@pytest.mark.parametrize('ignore_sigterm', [False, True])
def test_cancelled_trickle_download_releases_warm_worker_lock_and_continues_queue(tmp_path, ignore_sigterm):
    marker = tmp_path / 'socket-read-started'
    processes = []
    def spawn(command, **kwargs):
        process = subprocess.Popen([
            sys.executable, '-c', _TRICKLE_WORKER,
            command[-1], str(marker), str(int(ignore_sigterm and not processes)),
        ], **kwargs)
        processes.append(process)
        return process

    controller = controller_for(tmp_path, popen_factory=spawn)
    first = queued_job(controller, 'upload')
    blocked = queued_job(controller)
    try:
        controller._supervise_once()
        wait_for(marker.exists)
        process = processes[0]
        assert controller._active_job_id == first['id']
        assert controller.store.get_job(first['id'])['status'] == 'done'
        assert controller.store.get_job(blocked['id'])['worker_pid'] == process.pid
        assert os.getpgid(process.pid) == process.pid
        with pytest.raises(RuntimeError, match='Another echoscript worker'):
            _acquire_worker_lock(controller.settings.worker_lock_path)

        following = queued_job(controller, 'upload')
        controller.store.cancel(blocked['id'])
        controller._supervise_once()
        assert process.poll() == -(signal.SIGKILL if ignore_sigterm else signal.SIGTERM)
        with _acquire_worker_lock(controller.settings.worker_lock_path):
            pass

        controller._supervise_once()
        assert controller.store.get_job(blocked['id'])['status'] == 'cancelled'
        assert len(processes) == 2
        wait_for(lambda: processes[1].poll() is not None)
        controller._supervise_once()
        assert controller.store.get_job(following['id'])['status'] == 'done'
        assert controller._process is None
    finally:
        controller.stop()
