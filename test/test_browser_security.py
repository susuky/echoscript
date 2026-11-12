import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from echoscript.config import Settings
from echoscript.web import LocalJobController, create_web_app


@pytest.fixture
def workspace(tmp_path):
    controller = LocalJobController(Settings(
        data_dir=tmp_path, db_path=tmp_path / 'jobs.sqlite3', jobs_dir=tmp_path / 'jobs',
    ))
    with patch.object(controller, 'start'), patch.object(controller, 'stop'):
        with TestClient(create_web_app(controller), base_url='http://localhost') as client:
            yield client, controller


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://attacker.example'},
    {'Origin': 'null'},
    {'Origin': 'https://localhost'},
    {'Origin': 'http://localhost:81'},
    {'Origin': 'http://localhost.attacker.example'},
    {'Origin': 'http://localhost@attacker.example'},
    {'Origin': 'http://localhost/path'},
    {'Origin': 'http://localhost:invalid'},
    {'Sec-Fetch-Site': 'cross-site'},
])
def test_external_sites_cannot_create_jobs(workspace, headers):
    client, controller = workspace
    response = client.post('/api/jobs', headers=headers,
                           json={'source_type': 'upload', 'filename': 'recording.wav'})
    assert response.status_code == 403
    assert controller.store.list_jobs() == []
    assert response.headers['cache-control'] == 'no-store'


def test_plain_html_form_cannot_create_a_job(workspace):
    client, controller = workspace
    payload = json.dumps({'source_type': 'upload', 'filename': 'name=value.wav'})
    response = client.post('/api/jobs', content=payload, headers={
        'Content-Type': 'text/plain', 'Origin': 'https://attacker.example', 'Sec-Fetch-Site': 'cross-site',
    })
    assert response.status_code == 403
    assert controller.store.list_jobs() == []
    assert client.post('/api/jobs', content=payload, headers={'Content-Type': 'text/plain'}).status_code == 415
    assert controller.store.list_jobs() == []


@pytest.mark.parametrize('headers', [{}, {'Origin': 'http://localhost'}, {'Origin': 'http://localhost:80'},
                                     {'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'}])
def test_same_origin_and_command_line_clients_can_submit(workspace, headers):
    client, controller = workspace
    response = client.post('/api/jobs', headers=headers,
                           json={'source_type': 'upload', 'filename': 'recording.wav'})
    assert response.status_code == 201
    assert controller.job(response.json()['id'])['status'] == 'uploading'


def test_origin_check_covers_upload_settings_and_cancellation(workspace):
    client, controller = workspace
    job = client.post('/api/jobs', json={'source_type': 'upload', 'filename': 'recording.wav'}).json()
    headers = {'Origin': 'https://attacker.example'}
    assert client.put(f"/api/jobs/{job['id']}/media", content=b'audio', headers=headers).status_code == 403
    assert controller.job(job['id'])['status'] == 'uploading'
    assert client.put('/api/settings/openai', json={'api_key': 'sk-test-sentinel'}, headers=headers).status_code == 403
    assert controller.settings.get_openai_api_key() is None
    assert client.put(f"/api/jobs/{job['id']}/media", content=b'audio').status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/cancel", headers=headers).status_code == 403
    assert controller.job(job['id'])['status'] == 'queued'
    assert client.post(f"/api/jobs/{job['id']}/cancel", headers={'Origin': 'http://localhost'}).status_code == 200


@pytest.mark.parametrize('path', ['/', '/api/config', '/api/jobs'])
def test_rebinding_hostname_is_rejected_even_with_matching_origin(workspace, path):
    client, _ = workspace
    response = client.get(path, headers={
        'Host': 'attacker.example', 'Origin': 'http://attacker.example', 'Sec-Fetch-Site': 'same-origin',
    })
    assert response.status_code == 400
    assert response.headers['x-frame-options'] == 'DENY'
    assert 'jobs' not in response.json()


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1:7860', '[::1]:7860', '192.168.1.20:7860'])
def test_local_and_ip_hosts_remain_usable(workspace, host):
    client, _ = workspace
    assert client.get('/api/config', headers={'Host': host}).status_code == 200


@pytest.mark.parametrize('host', ['[invalid', 'localhost:invalid', 'localhost/path', 'user@localhost'])
def test_malformed_host_is_rejected_without_server_error(workspace, host):
    client, _ = workspace
    response = client.get('/api/config', headers={'Host': host})
    assert response.status_code == 400
    assert response.headers['cache-control'] == 'no-store'


def test_proxy_hostname_can_be_explicitly_trusted(tmp_path, monkeypatch):
    monkeypatch.setenv('ECHOSCRIPT_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ECHOSCRIPT_TRUSTED_HOSTS', ' transcription.example, TRANSCRIPTION.INTERNAL ')
    settings = Settings.from_env()
    assert settings.trusted_hosts == ('transcription.example', 'transcription.internal')
    controller = LocalJobController(settings)
    with patch.object(controller, 'start'), patch.object(controller, 'stop'):
        with TestClient(create_web_app(controller), base_url='https://transcription.example') as client:
            assert client.get('/api/config').status_code == 200
            response = client.post('/api/jobs', headers={'Origin': 'https://transcription.example'},
                                   json={'source_type': 'upload', 'filename': 'recording.wav'})
            assert response.status_code == 201
            assert client.get('/api/config', headers={'Host': 'other.example'}).status_code == 400
