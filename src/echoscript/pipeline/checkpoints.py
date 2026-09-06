from __future__ import annotations

from array import array
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import wave


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def job_lock(directory: Path):
    # The lock inode must survive deletion of an expired job directory, so a
    # concurrent resume cannot acquire a newly created replacement lock.
    locks = directory.parent / '.locks'
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / directory.name).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_edits(directory: Path) -> dict:
    """The published revision and its edit ledger share one atomic pointer."""
    pointer = directory / 'current.json'
    if pointer.is_file():
        current = load_json(pointer)
        if 'edits' in current:
            return current['edits']
    legacy = directory / 'edits.json'
    return load_json(legacy) if legacy.is_file() else {}


def plan_chunks(audio_path: Path, directory: Path, options, check_cancel=lambda: None) -> dict:
    """Persist sample-exact contiguous ranges; search a bounded quiet cut window."""
    digest = hashlib.sha256()
    with audio_path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            check_cancel()
            digest.update(block)
    config = options.to_dict()
    for key in ('output_formats', 'zh_script', 'diarize', 'min_speakers', 'max_speakers', 'timestamps'):
        config.pop(key, None)
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    path = directory / 'chunks.json'
    if path.exists():
        saved = load_json(path)
        if saved['audio_sha256'] != digest.hexdigest() or saved['options_sha256'] != signature:
            raise ValueError('Saved chunks do not match audio or recognition options')
        with wave.open(str(audio_path), 'rb') as audio:
            cursor = 0
            for index, chunk in enumerate(saved['chunks']):
                if (chunk['id'] != f'{index:06d}' or chunk['start_frame'] != cursor
                        or not isinstance(chunk['end_frame'], int) or chunk['end_frame'] <= cursor
                        or chunk['start'] != cursor / audio.getframerate()
                        or chunk['end'] != chunk['end_frame'] / audio.getframerate()):
                    raise ValueError('Saved chunk ranges have gaps, overlap or invalid boundaries')
                cursor = chunk['end_frame']
            if cursor != audio.getnframes() or saved['duration'] != cursor / audio.getframerate():
                raise ValueError('Saved chunk ranges do not cover the complete audio')
        return saved
    with wave.open(str(audio_path), 'rb') as audio:
        rate, count = audio.getframerate(), audio.getnframes()
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or count <= 0:
            raise ValueError('Expected nonempty mono PCM16 audio')
        seconds = options.chunk_seconds or (60 if options.asr_backend == 'qwen' else 300)
        target = int(seconds * rate)
        cursor = 0
        chunks = []
        while cursor < count:
            check_cancel()
            end = min(count, cursor + target)
            if options.chunk_strategy == 'energy' and end < count:
                # Search before the maximum, never force all languages into short windows.
                left = max(cursor + target // 2, end - 5 * rate)
                audio.setpos(left)
                samples = array('h', audio.readframes(end - left))
                if sys.byteorder != 'little':
                    samples.byteswap()
                window = max(1, rate // 20)
                candidates = range(0, max(1, len(samples) - window + 1), window)
                cut = min(candidates, key=lambda i: sum(v*v for v in samples[i:i+window]))
                end = left + cut + window // 2
            chunks.append({'id': f'{len(chunks):06d}', 'start_frame': cursor, 'end_frame': end,
                           'start': cursor/rate, 'end': end/rate, 'asr_status': 'pending',
                           'alignment_status': 'pending', 'diagnostics': {}})
            cursor = end
    result = {'version': 1, 'audio_sha256': digest.hexdigest(), 'options_sha256': signature,
              'sample_rate': rate, 'duration': count/rate, 'chunks': chunks}
    write_json(path, result)
    return result


def extract_chunk(audio_path: Path, destination: Path, chunk: dict) -> dict:
    square_sum = 0
    maximum = 0
    activity = []
    threshold_dbfs = -50.0
    threshold_squared = (32768 * 10 ** (threshold_dbfs / 20)) ** 2
    frames = chunk['end_frame'] - chunk['start_frame']
    temporary = destination.with_suffix('.part.wav')
    try:
        with wave.open(str(audio_path), 'rb') as source, wave.open(str(temporary), 'wb') as output:
            output.setparams(source.getparams())
            source.setpos(chunk['start_frame'])
            rate = source.getframerate()
            window_frames = max(1, rate // 10)
            remaining = frames
            while remaining:
                data = source.readframes(min(remaining, window_frames))
                if not data:
                    raise ValueError('Audio ended before saved chunk boundary')
                samples = array('h', data)
                if sys.byteorder != 'little':
                    samples.byteswap()
                maximum = max(maximum, max(abs(v) for v in samples))
                window_square_sum = sum(v*v for v in samples)
                square_sum += window_square_sum
                if window_square_sum / len(samples) >= threshold_squared:
                    start = (frames - remaining) / rate
                    end = (frames - remaining + len(samples)) / rate
                    # Bridge at most two analysis windows, so a short consonant
                    # closure does not split an otherwise continuous activity span.
                    if activity and start - activity[-1]['end'] <= 0.2 + 1e-9:
                        activity[-1]['end'] = end
                    else:
                        activity.append({'start': start, 'end': end})
                remaining -= len(samples)
                output.writeframesraw(data)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    rms = math.sqrt(square_sum/max(frames, 1))/32768
    return {'rms_dbfs': round(20*math.log10(max(rms, 1e-12)), 2),
            'peak': maximum/32768, 'digital_silence': maximum == 0,
            'activity_intervals': activity,
            'activity_analysis': {'method': 'pcm_rms', 'threshold_dbfs': threshold_dbfs,
                                  'window_seconds': window_frames / rate, 'merge_gap_seconds': 0.2,
                                  'time_reference': 'chunk_relative', 'speech_classification': False}}


def progress(manifest: dict) -> dict:
    chunks = manifest['chunks']
    return {'total': len(chunks), 'recognized': sum(c['asr_status'] == 'done' for c in chunks),
            'aligned': sum(c['alignment_status'] == 'done' for c in chunks),
            'failed': sum(c['asr_status'] == 'failed' or c['alignment_status'] == 'failed' for c in chunks),
            'processed_seconds': sum(c['end']-c['start'] for c in chunks if c['asr_status'] == 'done'),
            'duration': manifest['duration']}
