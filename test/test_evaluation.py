"""Synthetic unit examples only; these tests do not measure model accuracy."""
from __future__ import annotations

import json

import pytest

from echoscript.evaluation import edit_alignment, load_manifest, main, run_matrix, score, text_metrics


def test_exact_metrics_isolate_recognition_from_rendering_and_numbers():
    reference = {"human_verified": True, "provenance": "synthetic unit test",
                 "raw_text": "软件费用 $1.50", "display_text": "軟體費用 $1.50", "language": "zh",
                 "terms": ["软件"], "numbers": ["$1.50"]}
    manifest = {"cases": [{"id": "test", "reference": reference}]}
    report = score(manifest, {"runs": [{"case_id": "test", "policy_id": "qwen", "options": {"zh_script": "twp"},
                  "raw": {"text": "软件费用 $1.50"}, "display": {"text": "軟件費用 150"}}]})
    result = report["results"][0]
    assert result["asr"]["cer"]["errors"] == 0
    assert result["postprocessing_on_reference"]["cer"]["errors"] == 0
    assert result["final"]["cer_verbatim"]["errors"] > 0
    assert result["final"]["numbers"]["missing"] == {"$1.50": 1}
    assert result["terms"]["软件"] == {"expected": 1, "observed": 1}
    assert text_metrics("hello", "")["cer"]["deletions"] == 5
    assert text_metrics("", "noise")["cer"]["rate"] is None
    assert text_metrics("$1.50", "150")["cer_verbatim"]["errors"] == 2


def test_omissions_timing_and_complementarity_never_enable_automatic_correction():
    reference = {"human_verified": True, "raw_text": "日本語 中文", "provenance": "synthetic",
                 "utterances": [{"id": "ja", "text": "日本語", "start": 0.0, "end": 1.0},
                                {"id": "zh", "text": "中文", "start": 2.0, "end": 3.0}]}
    report = score({"cases": [{"id": "mixed", "reference": reference}]}, {"runs": [
        {"case_id": "mixed", "policy_id": "left", "raw": {"text": "日本語", "segments": [
            {"words": [{"text": "日本語", "start": 0.2, "end": 1.1}]}]}},
        {"case_id": "mixed", "policy_id": "right", "raw": {"text": "中文"}},
    ]})
    result = report["results"][0]
    assert result["utterances"][0]["timestamp"]["start_error_seconds"] == pytest.approx(0.2)
    assert result["utterances"][1]["suspected_omitted"]
    assert result["utterances"][1]["timestamp"]["status"] == "unmatched_or_unaligned_boundary"
    assert result["integrity"]["trailing_deleted_characters"] == 2
    paired = report["complementarity"][0]["paired_cases"][0]
    assert paired["left_only_reference_errors"] == 2
    assert paired["right_only_reference_errors"] == 3
    assert not report["automatic_cross_model_correction"]["enabled"]


def test_manifest_missing_reference_and_unknown_policy_are_explicit(tmp_path):
    path = tmp_path / "manifest.json"
    manifest = {"schema_version": 1, "cases": [{"id": "pending", "audio": "pending.wav"}],
                "policies": [{"id": "baseline", "options": {}}]}
    path.write_text(json.dumps(manifest))
    assert main(["validate", str(path)]) == 0
    report = score(load_manifest(path), {"runs": [{"case_id": "pending", "policy_id": "baseline"}]})
    assert report["results"][0]["status"] == "missing_human_reference"
    manifest["policies"][0]["options"]["typo"] = True
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unknown JobOptions"):
        load_manifest(path)
    with pytest.raises(ValueError, match="too long"):
        edit_alignment("a" * 5001, "a" * 5001)


def test_author_subtitle_agreement_is_not_claimed_as_verified_accuracy():
    report = score({"cases": [{"id": "author", "reference": {
        "raw_text": "caption", "source_kind": "author_subtitle", "human_verified": False,
        "provenance": "synthetic author-track fixture"}}]}, {"runs": [
            {"case_id": "author", "policy_id": "baseline", "raw": {"text": "caption"}}]})
    assert report["results"][0]["status"] == "scored_against_author_subtitle"
    assert report["results"][0]["reference_standard"] == "author_subtitle_not_verbatim_audited"


def test_matrix_reuses_complete_runs_and_rejects_changed_audio(tmp_path, monkeypatch):
    from echoscript.pipeline.pipeline import TranscriptionPipeline
    from echoscript.worker.model_manager import ModelManager

    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"synthetic fixture; fake pipeline does not read audio")
    manifest_path = tmp_path / "manifest.json"
    manifest = {"schema_version": 1, "cases": [{"id": "case", "audio": audio.name}],
                "policies": [{"id": "baseline", "options": {"timestamps": False}, "cost_per_hour": 1}]}
    manifest_path.write_text(json.dumps(manifest))
    calls = []
    def run(self, job):
        calls.append(job["id"])
        result = self.settings.jobs_dir / job["id"] / "asr.json"
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({"text": "sample", "duration": 2}))
        return result
    monkeypatch.setattr(TranscriptionPipeline, "run", run)
    monkeypatch.setattr(ModelManager, "unload_all", lambda self: None)
    output = tmp_path / "output"
    first = run_matrix(manifest, manifest_path, output)
    second = run_matrix(manifest, manifest_path, output)
    assert calls == ["case--baseline"]
    assert first == second
    assert first["runs"][0]["runtime"]["estimated_cost"] >= 0
    import hashlib
    import zipfile
    provenance = first["runs"][0]["provenance"]
    digest = hashlib.sha256()
    with zipfile.ZipFile(output / provenance["source_archive"]) as archive:
        for name in sorted(archive.namelist()):
            digest.update(name.removeprefix("src/echoscript/").encode())
            digest.update(archive.read(name))
    assert digest.hexdigest() == provenance["source_sha256"]
    audio.write_bytes(b"changed")
    with pytest.raises(ValueError, match="configuration changed"):
        run_matrix(manifest, manifest_path, output)


def test_repeated_text_in_an_unaligned_segment_cannot_borrow_later_timestamps():
    reference = {'human_verified': True, 'provenance': 'synthetic', 'raw_text': 'hello hello',
                 'utterances': [{'id':'first', 'text':'hello', 'start':0, 'end':1},
                                {'id':'second', 'text':'hello', 'start':3, 'end':4}]}
    raw = {'text':'hello hello', 'segments':[
        {'text':'hello', 'alignment':'unavailable', 'words':[]},
        {'text':'hello', 'alignment':'available', 'words':[{'text':'hello', 'start':3.1, 'end':4.2}]},
    ]}
    report = score({'cases':[{'id':'repeated', 'reference':reference}]}, {'runs':[
        {'case_id':'repeated', 'policy_id':'test', 'raw':raw},
    ]})
    utterances = report['results'][0]['utterances']
    assert utterances[0]['timestamp']['status'] == 'unmatched_or_unaligned_boundary'
    assert utterances[1]['timestamp']['start_error_seconds'] == pytest.approx(0.1)
    assert utterances[1]['timestamp']['end_error_seconds'] == pytest.approx(0.2)


def test_author_subtitle_times_and_empty_captions_do_not_become_human_truth():
    manifest = {'cases':[{'id':'author', 'reference':{
        'raw_text':'caption', 'source_kind':'author_subtitle', 'human_verified':False,
        'provenance':'synthetic author subtitle', 'utterances':[{'text':'caption', 'start':0, 'end':2}],
    }}, {'id':'empty', 'reference':{'raw_text':'', 'source_kind':'author_subtitle',
                                  'human_verified':False, 'provenance':'synthetic empty caption'}}]}
    report = score(manifest, {'runs':[
        {'case_id':'author', 'policy_id':'test', 'raw':{'text':'caption', 'segments':[
            {'text':'caption', 'words':[{'text':'caption', 'start':0, 'end':2}]}]}},
        {'case_id':'empty', 'policy_id':'test', 'raw':{'text':''}},
    ]})
    assert report['results'][0]['utterances'][0]['timestamp']['status'] == 'reference_times_not_human_verified'
    assert report['results'][1]['integrity']['verified_silence'] is False


def test_complementarity_checks_audio_identity_and_partial_inference_is_explicit():
    manifest = {'cases':[{'id':'case', 'reference':{'raw_text':'hello', 'human_verified':True, 'provenance':'synthetic'}}]}
    report = score(manifest, {'runs':[
        {'case_id':'case', 'policy_id':'a', 'audio_sha256':'a'*64, 'raw':{'text':'hell', 'metadata':{'integrity':{'complete':False}}}},
        {'case_id':'case', 'policy_id':'b', 'audio_sha256':'b'*64, 'raw':{'text':'hello'}},
    ]})
    assert report['results'][0]['inference_integrity']['asr'] == 'incomplete'
    assert report['complementarity'] == []
    assert report['excluded_complementarity_pairs'][0]['reason'] == 'audio_checksum_mismatch'


def test_matrix_retries_partial_runs_and_accumulates_cost(tmp_path, monkeypatch):
    from echoscript.pipeline.pipeline import TranscriptionPipeline
    from echoscript.worker.model_manager import ModelManager

    audio = tmp_path/'sample.wav'
    audio.write_bytes(b'synthetic')
    manifest = {'schema_version':1, 'cases':[{'id':'case', 'audio':audio.name}],
                'policies':[{'id':'baseline', 'options':{'timestamps':False}, 'cost_per_hour':2}]}
    manifest_path = tmp_path/'manifest.json'
    manifest_path.write_text(json.dumps(manifest))
    calls = []
    def run(self, job):
        calls.append(job['id'])
        result = self.settings.jobs_dir/job['id']/'asr.json'
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps({'text':'sample', 'duration':2, 'metadata':{
            'integrity':{'complete':len(calls) > 1}}}))
        return result
    monkeypatch.setattr(TranscriptionPipeline, 'run', run)
    monkeypatch.setattr(ModelManager, 'unload_all', lambda self:None)
    first = run_matrix(manifest, manifest_path, tmp_path/'output')['runs'][0]
    second = run_matrix(manifest, manifest_path, tmp_path/'output')['runs'][0]
    assert first['status'] == 'partial'
    assert second['status'] == 'complete'
    assert len(calls) == len(second['runtime']['attempts']) == 2
    assert second['runtime']['elapsed_seconds'] == sum(item['elapsed_seconds'] for item in second['runtime']['attempts'])
    assert second['runtime']['estimated_cost'] == second['runtime']['elapsed_seconds']/3600*2


def test_multi_policy_matrix_uses_isolated_processes_and_retains_failures(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    manifest = {"schema_version": 1, "cases": [{"id": "case", "audio": "unused.wav"}],
                "policies": [{"id": "qwen"}, {"id": "whisper"}]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    calls = []
    def child(command, **kwargs):
        calls.append(command)
        assert kwargs.get("shell") is None
        policy = command[command.index("--policy") + 1]
        output = Path(command[command.index("--output-dir") + 1])
        (output / "hypotheses.json").write_text(json.dumps({"runs": [
            {"case_id": "case", "policy_id": policy, "raw": {"text": "ok" if policy == "qwen" else "stale"}}]}))
        return subprocess.CompletedProcess(command, 0 if policy == "qwen" else 2)
    monkeypatch.setattr(subprocess, "run", child)
    result = run_matrix(manifest, manifest_path, tmp_path / "output")
    assert len(calls) == 2
    assert len({command[-1] for command in calls}) == 2
    assert result["runs"][0]["raw"]["text"] == "ok"
    assert result["runs"][1]["error"]["type"] == "PolicyProcessFailed"
    assert result["policy_processes"][1]["returncode"] == 2
