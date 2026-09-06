import json
from pathlib import Path
import wave
from types import SimpleNamespace

import pytest

from echoscript.pipeline.checkpoints import load_json, plan_chunks, write_json
from echoscript.pipeline.pipeline import TranscriptionPipeline
from echoscript.schema import JobOptions, Transcript, TranscriptSegment
from echoscript.storage.jobs import JobCancelled, JobStore


def recording(path, seconds=12):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x10\x00' * (16000 * seconds))
    return path


class Model:
    def __init__(self):
        self.asr_calls = []
        self.align_calls = []
        self.fail_asr = set()
        self.fail_align = set()

    def transcribe(self, path, **kwargs):
        key = Path(path).stem
        self.asr_calls.append(key)
        if key in self.fail_asr:
            raise RuntimeError('recognition failed')
        return Transcript(text=f'片段{key}', duration=kwargs['duration'], language='zh',
                          segments=[TranscriptSegment(0, kwargs['duration'], f'片段{key}', alignment='unavailable')])

    def align(self, path, transcript, **kwargs):
        key = Path(path).stem
        self.align_calls.append(key)
        if key in self.fail_align:
            raise RuntimeError('aligner failed')
        transcript.segments[0].alignment = 'available'
        transcript.metadata['alignment'] = {'status': 'available'}
        return transcript


def setup(tmp_path):
    model = Model()
    settings = SimpleNamespace(jobs_dir=tmp_path/'jobs', max_media_duration_seconds=100,
                               max_remote_download_bytes=10000, ffprobe_bin='ffprobe', ffmpeg_bin='ffmpeg',
                               release_between_stages=False)
    directory = settings.jobs_dir/'testjob'
    directory.mkdir(parents=True)
    audio = recording(directory/'audio.wav')
    job = {'id':'testjob', 'source_type':'upload', 'source_value':'audio.wav', 'media_path':str(audio),
           'options':JobOptions(chunk_seconds=5, chunk_strategy='fixed').to_dict()}
    pipeline = TranscriptionPipeline(settings, SimpleNamespace(get_transcriber=lambda _: model))
    return pipeline, job, model, directory


def test_failure_isolation_and_alignment_only_resume(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    model.fail_align = {'000001'}
    result = load_json(pipeline.run(job))
    assert model.asr_calls == ['000000', '000001', '000002']
    assert [s['alignment'] for s in result['segments']] == ['available','unavailable','available']
    assert result['metadata']['subtitles']['accepted'] == 2
    assert '片段000001' in result['text']
    model.fail_align.clear()
    pipeline.run(job)
    assert len(model.asr_calls) == 3
    assert model.align_calls == ['000000','000001','000002','000001']
    assert load_json(directory/'chunks.json')['chunks'][1]['alignment_status'] == 'done'


def test_asr_failure_does_not_repeat_successful_neighbours(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    model.fail_asr = {'000001'}
    first = load_json(pipeline.run(job))
    assert first['metadata']['integrity']['complete'] is False
    assert first['metadata']['integrity']['planned_seconds'] == 12
    assert first['metadata']['integrity']['recognized_seconds'] == 7
    assert first['segments'][1]['diagnostics']['issues'] == ['asr_failed', 'possible_omission']
    model.fail_asr.clear()
    result = load_json(pipeline.run(job))
    assert model.asr_calls == ['000000','000001','000002','000001']
    assert result['metadata']['integrity']['complete'] is True


def test_cancellation_saves_asr_before_alignment(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    def cancel(stage):
        if stage == 'aligning':
            raise JobCancelled()
    with pytest.raises(JobCancelled):
        pipeline.run(job, on_stage=cancel)
    assert (directory/'chunks/000000.asr.json').is_file()
    assert not model.align_calls
    pipeline.run(job)
    assert model.asr_calls == ['000000','000001','000002']


def test_chunks_cover_every_sample_and_reject_changed_or_missing_ranges(tmp_path):
    audio = recording(tmp_path/'audio.wav')
    options = JobOptions(chunk_seconds=5)
    manifest = plan_chunks(audio, tmp_path, options)
    assert manifest['chunks'][0]['start'] == 0
    assert manifest['chunks'][-1]['end'] == 12
    assert all(a['end_frame'] == b['start_frame'] for a,b in zip(manifest['chunks'],manifest['chunks'][1:]))
    manifest['chunks'].pop(1)
    write_json(tmp_path/'chunks.json', manifest)
    with pytest.raises(ValueError, match='ranges'):
        plan_chunks(audio, tmp_path, options)
    with pytest.raises(ValueError, match='options'):
        plan_chunks(audio, tmp_path, JobOptions(chunk_seconds=6))


def test_cancel_resume_and_late_complete_are_atomic(tmp_path):
    store = JobStore(tmp_path/'jobs.db')
    job = store.create_job(source_type='upload', source_value='x', media_path='x', options=JobOptions())
    assert store.cancel(job['id'])
    assert store.claim_next_job() is None
    assert store.resume(job['id'])
    store.claim_next_job()
    assert store.cancel(job['id'])
    with pytest.raises(JobCancelled):
        store.set_stage(job['id'], 'aligning')
    store.complete(job['id'], 'result.json')
    assert store.get_job(job['id'])['status'] == 'cancelled'
    assert store.resume(job['id'])
    assert not store.get_job(job['id'])['cancel_requested']


def test_saved_edits_apply_only_to_the_original_segment_text(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    pipeline.run(job)
    pointer = load_json(directory/'current.json')
    pointer['edits'] = {
        '000000:0': {'text':'已確認的修正', 'raw_text':'片段000000'},
        '000001:0': {'text':'不可套錯位置', 'raw_text':'不同的原文'},
        'removed:0': {'text':'保留待確認', 'raw_text':'舊段落'},
    }
    write_json(directory/'current.json', pointer)
    result = load_json(pipeline.run(job))
    assert '已確認的修正' in result['text']
    assert '不可套錯位置' not in result['text']
    assert result['segments'][0]['raw_text'] == '片段000000'
    assert {item['reason'] for item in result['metadata']['unapplied_edits']} == {
        'source_text_changed', 'segment_not_found',
    }
    assert len(model.asr_calls) == 3


def test_recognition_pass_finishes_before_aligner_residency_can_affect_later_chunks(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    events = []
    resident = [True]
    original_asr, original_align = model.transcribe, model.align

    def release_aligner():
        resident[0] = False
        events.append('release')

    def recognize(path, **kwargs):
        assert not resident[0], 'Prior alignment weights must not occupy ASR memory'
        events.append('asr:' + Path(path).stem)
        return original_asr(path, **kwargs)

    def align(path, transcript, **kwargs):
        assert all((directory / 'chunks' / f'{index:06d}.asr.json').exists() for index in range(3))
        resident[0] = True
        events.append('align:' + Path(path).stem)
        return original_align(path, transcript, **kwargs)

    model.release_aligner = release_aligner
    model.transcribe = recognize
    model.align = align
    pipeline.run(job)
    assert events == ['release', 'asr:000000', 'asr:000001', 'asr:000002',
                      'align:000000', 'align:000001', 'align:000002']


def test_cancel_mid_asr_pass_preserves_every_finished_checkpoint(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)

    def cancel(stage):
        if stage == 'transcribing' and len(model.asr_calls) == 2:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        pipeline.run(job, on_stage=cancel)
    assert not model.align_calls
    manifest = load_json(directory / 'chunks.json')
    assert [chunk['asr_status'] for chunk in manifest['chunks']] == ['done', 'done', 'pending']
    pipeline.run(job)
    assert model.asr_calls == ['000000', '000001', '000002']


def test_cached_unavailable_alignment_is_retried_without_repeating_asr(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    original_align = model.align
    failures = {'000001'}

    def align(path, transcript, **kwargs):
        part = original_align(path, transcript, **kwargs)
        if Path(path).stem in failures:
            part.segments[0].alignment = 'unavailable'
            part.metadata['alignment'] = {'status': 'unavailable', 'reason': 'alignment_failed'}
        return part

    model.align = align
    first = load_json(pipeline.run(job))
    assert first['segments'][1]['alignment'] == 'unavailable'
    assert (directory / 'chunks' / '000001.aligned.json').exists()
    failures.clear()
    second = load_json(pipeline.run(job))
    assert second['segments'][1]['alignment'] == 'available'
    assert model.asr_calls == ['000000', '000001', '000002']
    assert model.align_calls == ['000000', '000001', '000002', '000001']


def test_cancelled_first_run_publishes_saved_text_and_explicit_pending_gaps(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    def cancel(stage):
        if len(model.asr_calls) == 1:
            raise JobCancelled()
    with pytest.raises(JobCancelled):
        pipeline.run(job, on_stage=cancel)
    pointer = load_json(directory/'current.json')
    transcript = load_json(directory/'revisions'/pointer['revision']/'result.json')
    assert '片段000000' in transcript['text']
    assert transcript['metadata']['progress']['recognized'] == 1
    assert transcript['metadata']['integrity']['complete'] is False
    assert len(transcript['segments']) == 3
    assert transcript['segments'][-1]['diagnostics']['issues'] == ['pending']


def test_total_asr_failure_is_failed_but_result_gaps_remain_readable(tmp_path):
    pipeline, job, model, directory = setup(tmp_path)
    model.fail_asr = {'000000','000001','000002'}
    with pytest.raises(RuntimeError, match='No audio chunks'):
        pipeline.run(job)
    pointer = load_json(directory/'current.json')
    result = load_json(directory/'revisions'/pointer['revision']/'result.json')
    assert result['metadata']['progress']['recognized'] == 0
    assert len(result['metadata']['subtitles']['gaps']) == 3


def test_qwen_segment_limits_match_backend_capabilities():
    with pytest.raises(ValueError, match='backend limit'):
        JobOptions(chunk_seconds=181, timestamps=True)
    with pytest.raises(ValueError, match='backend limit'):
        JobOptions(chunk_seconds=1201, timestamps=False)
    assert JobOptions(chunk_seconds=1200, timestamps=False).chunk_seconds == 1200


@pytest.mark.parametrize(('later_activity', 'reliable_alignment', 'expected_warning'), [
    (True, True, True),
    (False, True, False),
    (True, False, False),
])
def test_acoustic_coverage_flags_only_uncovered_activity_with_reliable_timing(
    tmp_path, later_activity, reliable_alignment, expected_warning,
):
    from array import array
    import math
    import sys
    from echoscript.schema import TranscriptWord

    pipeline, job, model, directory = setup(tmp_path)
    job['options'] = JobOptions(chunk_seconds=30, chunk_strategy='fixed').to_dict()
    rate = 16000
    samples = array('h', (
        int((4000 if frame / rate < 3 else 200) * math.sin(2 * math.pi * 440 * frame / rate))
        if 1 <= frame / rate < 2 or later_activity and 6 <= frame / rate < 8 else 0
        for frame in range(12 * rate)
    ))
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(directory / 'audio.wav'), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(samples.tobytes())
    original_align = model.align

    def align(path, transcript, **kwargs):
        part = original_align(path, transcript, **kwargs)
        segment = part.segments[0]
        segment.start, segment.end = 1.1, 1.9
        segment.words = [TranscriptWord(1.1, 1.9, segment.text)]
        if not reliable_alignment:
            segment.alignment = 'unavailable'
            part.metadata['alignment'] = {'status': 'unavailable'}
        return part

    model.align = align
    result = load_json(pipeline.run(job))
    diagnostics = result['metadata']['chunks'][0]['diagnostics']
    assert ('possible_omission' in diagnostics['issues']) is expected_warning
    assert diagnostics['activity_analysis']['speech_classification'] is False
    if expected_warning:
        assert diagnostics['uncovered_activity']['intervals'] == [{'start': 6.0, 'end': 8.0}]
        assert diagnostics['uncovered_activity']['interpretation'] == 'possible_omission_or_non_speech_audio'
    else:
        assert 'uncovered_activity' not in diagnostics
