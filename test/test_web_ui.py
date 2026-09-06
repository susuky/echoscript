import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from echoscript.config import Settings
from echoscript.schema import JobOptions
from echoscript.web import LocalJobController, create_web_app


@pytest.fixture
def web_client(tmp_path):
    controller = LocalJobController(Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.sqlite3',
                                              jobs_dir=tmp_path/'jobs', max_upload_bytes=8))
    with patch.object(controller, 'start'), patch.object(controller, 'stop'):
        with TestClient(create_web_app(controller)) as client:
            yield client, controller


def create_upload(client, **options):
    response = client.post('/api/jobs', json={'source_type': 'upload', 'filename': '授業.wav', 'options': options})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_configuration_and_initial_history_are_real(web_client):
    client, _ = web_client
    config = client.get('/api/config').json()
    assert config['max_upload_bytes'] == 8
    assert config['default_options']['asr_backend'] == 'qwen'
    assert {'code': 'ja', 'label': '日文'} in config['languages']
    assert client.get('/api/jobs').json() == {'jobs': []}
    assert 'hf_token' not in config


def test_upload_stream_queues_only_when_complete_and_preserves_options(web_client):
    client, controller = web_client
    job_id = create_upload(client, language='ja', context='山田先生、認知行動療法')
    assert controller.job(job_id)['status'] == 'uploading'
    response = client.put(f'/api/jobs/{job_id}/media', content=b'audio')
    assert response.status_code == 200
    job = response.json()
    assert job['status'] == 'queued'
    assert job['options']['language'] == 'ja'
    assert job['options']['context'] == '山田先生、認知行動療法'
    assert not {'media_path', 'audio_path', 'result_path'} & job.keys()
    directory = controller.settings.jobs_dir / job_id
    assert (directory/'upload.wav').read_bytes() == b'audio'
    assert not list(directory.glob('*.part'))
    assert client.put(f'/api/jobs/{job_id}/media', content=b'new').status_code == 409


@pytest.mark.parametrize('chunked', [False, True])
def test_oversized_upload_is_rejected_before_retaining_file(web_client, chunked):
    client, controller = web_client
    job_id = create_upload(client)
    content = iter([b'1234', b'56789']) if chunked else b'123456789'
    response = client.put(f'/api/jobs/{job_id}/media', content=content)
    assert response.status_code == 413
    assert controller.job(job_id)['status'] == 'failed'
    assert not list((controller.settings.jobs_dir/job_id).glob('upload*'))


def test_upload_disconnect_is_failed_and_partial_removed(web_client):
    import asyncio
    from echoscript.api import create_api
    client, controller = web_client
    job_id = create_upload(client)
    app = create_api(controller)
    messages = iter([{'type': 'http.request', 'body': b'abcd', 'more_body': True}, {'type': 'http.disconnect'}])
    output = []
    async def receive():
        return next(messages, {'type': 'http.disconnect'})
    async def send(message):
        output.append(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
             'method': 'PUT', 'scheme': 'http', 'path': f'/api/jobs/{job_id}/media',
             'raw_path': f'/api/jobs/{job_id}/media'.encode(), 'query_string': b'', 'headers': [],
             'client': ('127.0.0.1', 123), 'server': ('testserver', 80)}
    asyncio.run(app(scope, receive, send))
    assert controller.job(job_id)['status'] == 'failed'
    assert not list((controller.settings.jobs_dir/job_id).glob('upload*'))


def test_done_result_is_downloaded_directly_without_temporary_copy(web_client):
    client, controller = web_client
    job_id = create_upload(client)
    directory = controller.settings.jobs_dir/job_id/'output'
    directory.mkdir(parents=True)
    transcript = {'text': '学校の研習，費用 $1.50。', 'segments': [], 'speakers': [], 'metadata': {'timestamps': False}}
    (directory/'result.json').write_text(json.dumps(transcript, ensure_ascii=False))
    (directory/'result.txt').write_text(transcript['text'])
    controller.store.complete(job_id, str(directory/'result.json'))
    result = client.get(f'/api/jobs/{job_id}/result')
    assert result.status_code == 200
    assert result.json()['text'] == transcript['text']
    assert {item['format'] for item in result.json()['files']} == {'txt', 'json'}
    download = client.get(f'/api/jobs/{job_id}/files/txt')
    assert download.text == transcript['text']
    assert download.headers['cache-control'] == 'no-store'
    assert client.get(f'/api/jobs/{job_id}/files/srt').status_code == 404


def test_pending_missing_and_malformed_jobs_are_explicit(web_client):
    client, _ = web_client
    job_id = create_upload(client)
    assert client.get(f'/api/jobs/{job_id}/result').status_code == 409
    assert client.get('/api/jobs/'+'f'*32).status_code == 404
    assert client.get('/api/jobs/invalid').status_code == 404
    assert client.post('/api/jobs', json={'source_type': 'upload', 'filename': 'a', 'options': {'asr_model': '/secret'}}).status_code == 400
    assert client.post('/api/jobs', content=b'x'*65537).status_code == 413


def test_error_text_hides_internal_paths(web_client):
    client, controller = web_client
    job_id = create_upload(client)
    controller.store.fail(job_id, 'RuntimeError /secret/model/path CUDA out of memory')
    response = client.get(f'/api/jobs/{job_id}')
    assert 'secret' not in response.text
    assert '資源不足' in response.json()['error']


@pytest.mark.parametrize(('error', 'message'), [
    ('ERROR: [youtube] video: Join this channel to get access to members-only content', '限頻道會員觀看'),
    ("ERROR: [youtube] video: Private video. Sign in if you've been granted access", '這是私人影片'),
    ('ERROR: unable to download video data: HTTP Error 403: Forbidden', 'YouTube 未允許此次下載'),
])
def test_youtube_access_errors_explain_local_download_instead_of_model_access(web_client, error, message):
    client, controller = web_client
    job_id = create_upload(client)
    controller.store.fail(job_id, error)
    visible = client.get(f'/api/jobs/{job_id}').json()['error']
    assert message in visible
    assert '下載' in visible and '上傳' in visible
    assert '辨識功能' not in visible


def test_japanese_chinese_profile_keeps_auto_language_and_user_terms(web_client):
    client, controller = web_client
    job_id = create_upload(client, language='ja-zh', context='山田先生、講義')
    options = controller.job(job_id)['options']
    assert options['language'] is None
    assert '日中雙語課程' in options['context']
    assert options['context'].endswith('山田先生、講義')


@pytest.mark.parametrize('chunked', [False, True])
def test_unlimited_upload_accepts_stream_and_still_rejects_empty(web_client, chunked):
    client, controller = web_client
    controller.settings = replace(controller.settings, max_upload_bytes=0)
    job_id = create_upload(client)
    content = iter([b'1234', b'56789']) if chunked else b'123456789'
    response = client.put(f'/api/jobs/{job_id}/media', content=content)
    assert response.status_code == 200
    assert response.json()['status'] == 'queued'
    empty = create_upload(client)
    assert client.put(f'/api/jobs/{empty}/media', content=b'').status_code == 400
