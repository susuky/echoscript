from unittest.mock import patch
from pathlib import Path
import wave

from fastapi.testclient import TestClient

from echoscript.config import Settings
from echoscript.pipeline.checkpoints import write_json, load_json
from echoscript.pipeline.normalize import normalize_transcript
from echoscript.pipeline.pipeline import publish_result
from echoscript.schema import JobOptions, Transcript, TranscriptSegment
from echoscript.web import LocalJobController, create_web_app


def test_review_audio_edit_revision_and_retry_keep_exports_consistent(tmp_path):
    controller = LocalJobController(Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.db', jobs_dir=tmp_path/'jobs'))
    job = controller.store.create_job(source_type='upload', source_value='review.wav', media_path=None, options=JobOptions())
    job_id = job['id']
    directory = tmp_path/'jobs'/job_id
    transcript = normalize_transcript(Transcript(text='软件很好。 缺口', duration=8, language='zh', segments=[
        TranscriptSegment(0, 3, '软件很好。', id='000000:0', diagnostics={'chunk_id':'000000'}),
        TranscriptSegment(3, 8, '缺口', id='000001:0', alignment='unavailable', diagnostics={'chunk_id':'000001'})]), 'tw')
    original = publish_result(directory, transcript, ['txt','srt','vtt','json'])
    controller.store.complete(job_id, str(original))
    with wave.open(str(directory/'audio.wav'), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0\0'*16000)
    manifest = {'duration':8, 'chunks':[{'id':f'{i:06d}', 'start':i*3, 'end':3 if i==0 else 8,
                'asr_status':'done', 'alignment_status':'done' if i==0 else 'failed', 'diagnostics':{}} for i in range(2)]}
    write_json(directory/'chunks.json', manifest)
    for i in range(2):
        write_json(directory/f'chunks/{i:06d}.asr.json', {'text':'saved'})
        write_json(directory/f'chunks/{i:06d}.aligned.json', {'text':'saved'})
    with patch.object(controller, 'start'), patch.object(controller, 'stop'), TestClient(create_web_app(controller)) as client:
        url = f'/api/jobs/{job_id}'
        data = client.get(url+'/result').json()
        revision = data['transcript']['metadata']['revision']
        assert data['transcript']['metadata']['subtitles']['status'] == 'partial'
        ranged = client.get(url+'/audio', headers={'Range':'bytes=0-43'})
        assert ranged.status_code == 206 and len(ranged.content) == 44
        changed = client.patch(url+'/segments/000000:0', json={'text':'修正後的文字。','revision':revision})
        assert changed.status_code == 200, changed.text
        result = changed.json()
        assert '修正後的文字。' in result['text']
        assert result['transcript']['raw_text'] == '软件很好。 缺口'
        assert result['transcript']['segments'][0]['raw_text'] == '软件很好。'
        for item in result['files']:
            body = client.get(item['url']).text
            assert '修正後的文字。' in body
            if item['format'] in {'srt','vtt'}:
                assert '缺口' not in body
            else:
                assert '缺口' in body
        assert client.patch(url+'/segments/000000:0', json={'text':'stale', 'revision':revision}).status_code == 409
        old_srt = next(f['url'] for f in data['files'] if f['format']=='srt')
        assert '修正後的文字。' not in client.get(old_srt).text
        revision = result['transcript']['metadata']['revision']
        resumed = client.post(url+'/resume', json={'stage':'alignment','chunk_ids':['000001'],'revision':revision})
        assert resumed.status_code == 200
        assert (directory/'chunks/000001.asr.json').exists()
        assert (directory/'chunks/000001.aligned.json').exists()
        assert (directory/'chunks/000000.aligned.json').exists()
        assert load_json(directory/'edits.json')['000000:0']['text'] == '修正後的文字。'
        assert client.patch(url+'/segments/000000:0', json={'text':'busy', 'revision':revision}).status_code == 409
        assert client.post(url+'/cancel').status_code == 200
        assert client.get(url).json()['status'] == 'cancelled'
        assert client.get(url+'/chunks').json()['progress']['recognized'] == 2
        changed_while_cancelled = client.patch(url+'/segments/000000:0', json={
            'text':'取消後仍可修正。', 'revision':revision,
        })
        assert changed_while_cancelled.status_code == 200, changed_while_cancelled.text
        assert '取消後仍可修正。' in changed_while_cancelled.json()['text']
        saved_job = controller.store.get_job(job_id)
        assert saved_job['status'] == 'cancelled'
        assert saved_job['cancel_requested'] == 1
        assert load_json(Path(saved_job['result_path']))['segments'][0]['text'] == '取消後仍可修正。'


def test_failed_publication_keeps_previous_revision_and_acknowledged_edits(tmp_path):
    import pytest
    from echoscript.pipeline.checkpoints import load_edits

    transcript = normalize_transcript(Transcript(text='原稿', segments=[TranscriptSegment(0, 1, '原稿', id='a')]), None)
    acknowledged = {'a': {'raw_text': '原稿', 'text': '已確認'}}
    original = publish_result(tmp_path, transcript, ['txt', 'srt'], edits=acknowledged)
    original_pointer = load_json(tmp_path/'current.json')
    pending = {'a': {'raw_text': '原稿', 'text': '尚未發佈'}}
    # The compatibility cache may have been written by the editor; it must not
    # become authoritative if rendering or publication fails.
    write_json(tmp_path/'edits.json', pending)
    with patch('echoscript.pipeline.pipeline.render_transcript', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            publish_result(tmp_path, transcript, ['txt', 'srt'], edits=pending)
    assert load_json(tmp_path/'current.json') == original_pointer
    assert load_edits(tmp_path) == acknowledged
    assert original.is_file()


def test_revision_download_rejects_a_symlink_outside_the_revision(tmp_path):
    controller = LocalJobController(Settings(data_dir=tmp_path, db_path=tmp_path/'jobs.db', jobs_dir=tmp_path/'jobs'))
    job = controller.store.create_job(source_type='upload', source_value='review.wav', media_path=None, options=JobOptions())
    directory = tmp_path/'jobs'/job['id']
    transcript = Transcript(text='原稿', segments=[TranscriptSegment(0, 1, '原稿')])
    old = publish_result(directory, transcript, ['txt'])
    previous_revision = transcript.metadata['revision']
    current = publish_result(directory, transcript, ['txt'])
    controller.store.complete(job['id'], str(current))
    outside = tmp_path/'outside.txt'
    outside.write_text('unrelated file')
    old.with_suffix('.txt').unlink()
    old.with_suffix('.txt').symlink_to(outside)
    with patch.object(controller, 'start'), patch.object(controller, 'stop'), TestClient(create_web_app(controller)) as client:
        response = client.get(f'/api/jobs/{job["id"]}/files/txt?revision={previous_revision}')
        assert response.status_code == 404
        assert 'unrelated file' not in response.text
