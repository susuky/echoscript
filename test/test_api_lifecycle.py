import asyncio
import threading
from unittest.mock import Mock, patch

import httpx

from echoscript.api import create_api
from echoscript.config import Settings
from echoscript.schema import JobOptions
from echoscript.web import LocalJobController


def test_duplicate_put_cannot_fail_an_upload_already_in_progress(tmp_path):
    controller = LocalJobController(Settings(
        data_dir=tmp_path, db_path=tmp_path / "jobs.sqlite3",
        jobs_dir=tmp_path / "jobs", max_upload_bytes=8,
    ))
    job = controller.begin_upload("audio.wav", JobOptions())
    route = f"/api/jobs/{job['id']}/media"

    async def exercise():
        first_chunk_written = asyncio.Event()
        finish_upload = asyncio.Event()

        async def media():
            yield b"ab"
            first_chunk_written.set()
            await finish_upload.wait()
            yield b"cd"

        transport = httpx.ASGITransport(app=create_api(controller), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            original = asyncio.create_task(client.put(route, content=media()))
            try:
                await asyncio.wait_for(first_chunk_written.wait(), timeout=5)
                # This would fail the shared job if size validation runs before
                # acquiring exclusive ownership of the upload's partial file.
                duplicate = await client.put(route, content=b"123456789")
                assert duplicate.status_code == 409
                assert controller.job(job["id"])["status"] == "uploading"
            finally:
                finish_upload.set()
                response = await asyncio.wait_for(original, timeout=5)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "queued"

    asyncio.run(exercise())
    stored = controller.store.get_job(job["id"])
    directory = controller.settings.jobs_dir / job["id"]
    assert stored["status"] == "queued"
    assert (directory / "upload.wav").read_bytes() == b"abcd"
    assert not list(directory.glob("*.part"))


def test_slow_supervisor_cannot_spawn_a_worker_after_stop_returns(tmp_path):
    spawn = Mock()
    controller = LocalJobController(Settings(
        data_dir=tmp_path, db_path=tmp_path / "jobs.sqlite3", jobs_dir=tmp_path / "jobs",
    ), popen_factory=spawn)
    job = controller.store.create_job(
        source_type="url", source_value="https://example.com/audio.wav",
        media_path=None, options=JobOptions(),
    )
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    def slow_cleanup():
        cleanup_started.set()
        release_cleanup.wait(timeout=10)

    with patch.object(controller, "_maybe_cleanup_expired_jobs", side_effect=slow_cleanup):
        controller.start()
        try:
            assert cleanup_started.wait(timeout=5)
            controller.stop()
            assert controller._stop.is_set()
            assert controller._dispatcher_lock is None
        finally:
            release_cleanup.set()
            controller._thread.join(timeout=5)

    assert not controller._thread.is_alive()
    spawn.assert_not_called()
    assert controller._process is None
    assert controller.store.get_job(job["id"])["status"] == "queued"
