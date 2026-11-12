import io
import json
import math
import stat
import struct
import wave
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from echoscript.config import Settings
from echoscript.web import LocalJobController, create_web_app
from echoscript.worker.worker import _run_locked_worker


@pytest.fixture
def openai_web(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "jobs.db", jobs_dir=tmp_path / "jobs")
    controller = LocalJobController(settings)
    with patch.object(controller, "start"), patch.object(controller, "stop"):
        with TestClient(create_web_app(controller), base_url="http://localhost") as client:
            yield client, controller


def _new_job(client, **options):
    return client.post("/api/jobs", json={
        "source_type": "upload", "filename": "demo.wav", "options": {"asr_backend": "openai", **options},
    })


def _audio(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 2 * math.pi * 220 / 16000)))
                                   for i in range(16000 * seconds)))
    return output.getvalue()


def test_key_is_private_persistent_and_not_in_api_responses_or_jobs(openai_web):
    client, controller = openai_web
    assert client.get("/api/config").json()["openai_api_key_configured"] is False
    response = _new_job(client)
    assert response.status_code == 400
    assert "API key" in response.json()["detail"]
    assert controller.store.list_jobs() == []

    key = "sk-private-test-sentinel"
    response = client.put("/api/settings/openai", json={"api_key": f" {key} "})
    assert response.status_code == 200
    assert response.json() == {"configured": True}
    saved = controller.settings.data_dir / "openai-api-key"
    assert saved.read_text() == key
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert client.get("/api/config").json()["openai_api_key_configured"] is True
    job = _new_job(client)
    assert job.status_code == 201, job.text
    assert job.json()["options"]["asr_model"] == "gpt-transcribe"
    assert job.json()["options"]["timestamps"] is False
    for url in ("/api/config", "/api/jobs", f"/api/jobs/{job.json()['id']}"):
        assert key not in client.get(url).text
    assert key not in json.dumps(controller.store.list_jobs())
    restarted = Settings(data_dir=saved.parent, db_path=saved.parent / "jobs.db", jobs_dir=saved.parent / "jobs")
    assert restarted.get_openai_api_key() == key
    assert client.get("/openai-api-key").status_code == 404


@pytest.mark.parametrize("payload", [
    {"api_key": ""}, {"api_key": "  "}, {"api_key": "sk-secret\r\nInjected: value"},
    {"api_key": "sk-secret\x00"}, {"api_key": "中文-sk-secret"},
    {"api_key": "sk-secret" + "x" * 512}, {"api_key": {"value": "sk-secret"}},
    {"api_key": "sk-secret", "extra": True},
])
def test_invalid_key_is_rejected_without_echoing_it(openai_web, payload):
    client, controller = openai_web
    response = client.put("/api/settings/openai", json=payload)
    assert response.status_code == 422
    assert "sk-secret" not in response.text
    assert controller.settings.get_openai_api_key() is None


def test_oversized_key_body_does_not_overwrite_saved_key(openai_web):
    client, controller = openai_web
    controller.settings.save_openai_api_key("sk-original")
    response = client.put("/api/settings/openai", content=b'{"api_key":"sk-secret' + b"x" * 4096 + b'"}')
    assert response.status_code == 422
    assert "sk-secret" not in response.text
    assert controller.settings.get_openai_api_key() == "sk-original"


def test_saved_key_overrides_environment_and_failed_save_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment")
    settings = Settings.from_env()
    assert settings.get_openai_api_key() == "sk-environment"
    assert "sk-environment" not in repr(settings)
    settings.save_openai_api_key("sk-saved")
    assert settings.get_openai_api_key() == "sk-saved"
    with patch("echoscript.config.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            settings.save_openai_api_key("sk-new")
    assert settings.get_openai_api_key() == "sk-saved"
    assert not list(tmp_path.glob(".openai-api-key-*"))


@pytest.mark.parametrize("status", [200, 401])
def test_upload_worker_and_exports_use_saved_key_with_real_audio_processing(openai_web, monkeypatch, status):
    client, controller = openai_web
    client.put("/api/settings/openai", json={"api_key": "sk-pipeline-sentinel"})
    response = _new_job(client, chunk_seconds=5)
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    assert client.put(f"/api/jobs/{job_id}/media", content=_audio(11 if status == 401 else 1)).status_code == 200
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer sk-pipeline-sentinel"
        assert b"RIFF" in request.read()
        assert b'gpt-transcribe' in request.content
        return httpx.Response(status, json={"text": "Hello EchoScript.", "languages": [{"code": "en"}]})

    def post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(upstream)) as transport:
            return transport.post(url, **kwargs)

    monkeypatch.setattr("echoscript.transcription.openai.httpx.post", post)
    assert _run_locked_worker(controller.settings, job_id) == 0
    assert len(requests) == 1
    job = client.get(f"/api/jobs/{job_id}").json()
    result = client.get(f"/api/jobs/{job_id}/result").json()
    if status == 200:
        assert job["status"] == "done", job
        assert result["transcript"]["text"] == "Hello EchoScript."
        assert {file["format"] for file in result["files"]} == {"txt", "json"}
        text_url = next(file["url"] for file in result["files"] if file["format"] == "txt")
        assert "Hello EchoScript." in client.get(text_url).text
    else:
        assert job["status"] == "failed"
        assert "API key" in job["error"]
        chunks = client.get(f"/api/jobs/{job_id}/chunks").json()["chunks"]
        assert len(chunks) > 1
        assert any(chunk["asr_status"] == "pending" for chunk in chunks)
    assert "sk-pipeline-sentinel" not in json.dumps(result)
    assert "sk-pipeline-sentinel" not in json.dumps(job)
