
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from echoscript.cli import cli
from echoscript.config import Settings
from echoscript.pipeline.pipeline import publish_result
from echoscript.schema import Transcript, TranscriptSegment
from echoscript.storage import JobStore
from echoscript.worker import run_worker


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def local_job(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path / "data"))
    for name in ("ECHOSCRIPT_DB_PATH", "ECHOSCRIPT_JOBS_DIR", "ECHOSCRIPT_WORKER_LOCK_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_TOKEN", "test-token")
    settings = Settings.from_env()
    media = tmp_path / "recording.wav"
    media.write_bytes(b"media")
    transcript = Transcript(
        text="Transcribed text", duration=3, language="English",
        segments=[TranscriptSegment(0, 2, "Transcribed text")],
        metadata={"integrity": {"complete": True}, "alignment": {"status": "available"}},
    )

    def process(job, on_stage=None):
        if on_stage:
            on_stage("transcribing")
        return publish_result(settings.jobs_dir / job["id"], transcript, job["options"]["output_formats"])

    with (patch("echoscript.worker.worker.ModelManager") as models,
          patch("echoscript.worker.worker.TranscriptionPipeline") as pipeline,
          patch("echoscript.cli.time.sleep")):
        pipeline.return_value.run.side_effect = process
        yield media, settings, pipeline.return_value, models.return_value, transcript


def test_cli_without_command(runner):
    result = runner.invoke(cli, [])
    assert result.exit_code == 1
    assert "--help" in result.output


@pytest.mark.parametrize("args", [["transcribe"], ["-a"]])
def test_default_and_compatibility_commands_use_the_current_worker(runner, local_job, args):
    media, settings, pipeline, models, _ = local_job
    result = runner.invoke(cli, [*args, str(media)])
    assert result.exit_code == 0, result.output
    assert result.stdout == "Transcribed text\n"
    job = pipeline.run.call_args.args[0]
    assert job["options"]["asr_backend"] == "qwen"
    assert job["options"]["asr_model"] == "Qwen/Qwen3-ASR-1.7B"
    assert "Job: " + job["id"] in result.stderr
    assert JobStore(settings.db_path).get_job(job["id"])["status"] == "done"
    assert Path(job["media_path"]).read_bytes() == media.read_bytes()
    models.unload_all.assert_called_once()


@pytest.mark.parametrize("arguments, backend, model, language", [
    (["--backend", "faster-whisper", "--language", "English"], "faster-whisper", "large-v3-turbo", "en"),
    (["-m", "tiny", "--lang", "zh-tw"], "faster-whisper", "tiny", "zh"),
    (["-m", "base.en", "--lang", "en"], "faster-whisper", "base.en", "en"),
    (["-m", "Qwen/Qwen3-ASR-0.6B", "-l", "ja_JP"], "qwen", "Qwen/Qwen3-ASR-0.6B", "Japanese"),
])
def test_backend_model_defaults_and_language_aliases(runner, local_job, arguments, backend, model, language):
    media, _, pipeline, _, _ = local_job
    result = runner.invoke(cli, ["transcribe", str(media), *arguments, "--quiet"])
    assert result.exit_code == 0, result.output
    options = pipeline.run.call_args.args[0]["options"]
    assert (options["asr_backend"], options["asr_model"], options["language"]) == (backend, model, language)
    assert result.stderr == ""


def test_pipeline_options_and_multiple_exports(runner, local_job, tmp_path):
    media, _, pipeline, _, _ = local_job
    output = tmp_path / "exports"
    result = runner.invoke(cli, [
        "transcribe", str(media), "-l", "ja-zh", "--context", "A lesson", "--glossary", "EchoScript",
        "--diarize", "--min-speakers", "2", "--max-speakers", "3", "--zh-script", "none",
        "--no-condition-on-previous-text", "--context-token-budget", "120", "--glossary-token-budget", "60",
        "--chunk-seconds", "30", "--chunk-strategy", "fixed", "--device", "cpu", "--compute-type", "int8",
        "-f", "all", "--output-dir", str(output),
    ])
    assert result.exit_code == 0, result.output
    options = pipeline.run.call_args.args[0]["options"]
    assert options["language"] is None
    assert "日中雙語課程" in options["context"] and options["context"].endswith("A lesson")
    for key, expected in {"glossary": "EchoScript", "diarize": True, "min_speakers": 2,
                          "max_speakers": 3, "zh_script": None, "condition_on_previous_text": False,
                          "context_token_budget": 120, "glossary_token_budget": 60,
                          "chunk_seconds": 30, "chunk_strategy": "fixed", "device": "cpu", "compute_type": "int8"}.items():
        assert options[key] == expected
    assert {path.name for path in output.iterdir()} == {f"result.{fmt}" for fmt in ("txt", "srt", "vtt", "json")}
    assert result.stdout == ""


def test_public_url_uses_the_existing_download_pipeline(runner, local_job):
    _, _, pipeline, _, _ = local_job
    result = runner.invoke(cli, ["transcribe", "https://93.184.216.34/media", "--quiet"])
    assert result.exit_code == 0, result.output
    job = pipeline.run.call_args.args[0]
    assert job["source_type"] == "url" and job["source_value"] == "https://93.184.216.34/media"
    assert job["media_path"] is None


@pytest.mark.parametrize("args, expected", [
    (["-f", "bad"], "Invalid value"),
    (["-l", "zz"], "Unsupported Qwen language"),
    (["--backend", "faster-whisper", "-l", "zz"], "Unsupported faster-whisper language"),
    (["--no-timestamps", "--diarize"], "requires timestamps"),
    (["--no-timestamps", "-f", "srt"], "requires --timestamps"),
    (["--min-speakers", "2"], "require --diarize"),
    (["--diarize", "--min-speakers", "3", "--max-speakers", "2"], "cannot exceed"),
    (["--chunk-seconds", "nan"], "must be finite"),
    (["--chunk-seconds", "181"], "backend limit"),
    (["-f", "txt", "-f", "json"], "require --output-dir"),
    (["-o", "one.txt", "--output-dir", "exports"], "either --output or --output-dir"),
])
def test_invalid_options_fail_before_model_loading(runner, local_job, args, expected):
    media, _, pipeline, models, _ = local_job
    result = runner.invoke(cli, ["transcribe", str(media), *args])
    assert result.exit_code != 0 and expected in result.output
    pipeline.run.assert_not_called()
    models.unload_all.assert_not_called()


def test_output_extension_and_json_stdout(runner, local_job, tmp_path):
    media, _, _, _, _ = local_job
    destination = tmp_path / "nested" / "transcript.json"
    result = runner.invoke(cli, ["transcribe", str(media), "-o", str(destination)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["text"] == "Transcribed text"
    assert destination.read_text() == result.stdout


@pytest.mark.parametrize("model", ["base", "large-v2", "local-whisper-model"])
def test_legacy_option_aliases_keep_whisper_selection(runner, local_job, tmp_path, model):
    media, _, pipeline, _, _ = local_job
    destination = tmp_path / "legacy.json"
    result = runner.invoke(cli, ["-a", str(media), "--model-name", model, "--fmt", "json",
                                 "--lang", "English", "--filename", str(destination), "--no-verbose"])
    assert result.exit_code == 0, result.output
    options = pipeline.run.call_args.args[0]["options"]
    assert (options["asr_backend"], options["asr_model"], options["language"]) == ("faster-whisper", model, "en")
    assert result.stdout == "" and json.loads(destination.read_text())["text"] == "Transcribed text"


def test_all_formats_without_timestamps_exports_only_text_and_json(runner, local_job, tmp_path):
    media, _, pipeline, _, transcript = local_job
    transcript.metadata["alignment"]["status"] = "disabled"
    transcript.segments[0].alignment = "disabled"
    destination = tmp_path / "untimed"
    result = runner.invoke(cli, ["transcribe", str(media), "--no-timestamps", "-f", "all", "--output-dir", str(destination)])
    assert result.exit_code == 0, result.output
    assert pipeline.run.call_args.args[0]["options"]["timestamps"] is False
    assert {path.name for path in destination.iterdir()} == {"result.txt", "result.json"}
    assert "Some subtitle timing" not in result.stderr


def test_output_protection_and_explicit_overwrite(runner, local_job, tmp_path):
    media, _, pipeline, _, _ = local_job
    destination = tmp_path / "result.txt"
    destination.write_text("existing")
    result = runner.invoke(cli, ["transcribe", str(media), "-o", str(destination)])
    assert result.exit_code == 1 and "--overwrite" in result.output
    assert destination.read_text() == "existing"
    pipeline.run.assert_not_called()
    result = runner.invoke(cli, ["transcribe", str(media), "-o", str(media), "--overwrite"])
    assert result.exit_code != 0 and media.read_bytes() == b"media"
    result = runner.invoke(cli, ["transcribe", str(media), "-o", str(destination), "--overwrite", "--no-verbose"])
    assert result.exit_code == 0 and result.stdout == ""
    assert destination.read_text() == "Transcribed text\n"


def test_file_created_during_transcription_is_not_overwritten(runner, local_job, tmp_path):
    media, _, pipeline, _, _ = local_job
    destination = tmp_path / "result.txt"
    process = pipeline.run.side_effect

    def late_file(job, **kwargs):
        destination.write_text("created while waiting")
        return process(job, **kwargs)

    pipeline.run.side_effect = late_file
    result = runner.invoke(cli, ["transcribe", str(media), "-o", str(destination)])
    assert result.exit_code == 1
    assert destination.read_text() == "created while waiting"
    assert list(tmp_path.glob("tmp*")) == []


def test_jobs_export_and_resume_reuse_the_saved_job(runner, local_job, tmp_path):
    media, settings, pipeline, _, _ = local_job
    assert runner.invoke(cli, ["transcribe", str(media), "--quiet"]).exit_code == 0
    listing = runner.invoke(cli, ["jobs"])
    [job] = json.loads(listing.stdout)
    assert job["status"] == "done" and "media_path" not in job
    assert json.loads(runner.invoke(cli, ["jobs", job["id"]]).stdout) == job
    destination = tmp_path / "copy.vtt"
    result = runner.invoke(cli, ["export", job["id"], "-o", str(destination), "--no-verbose"])
    assert result.exit_code == 0 and destination.read_text().startswith("WEBVTT")
    assert pipeline.run.call_count == 1
    result = runner.invoke(cli, ["resume", job["id"], "--quiet"])
    assert result.exit_code == 0, result.output
    assert pipeline.run.call_count == 2
    assert len(JobStore(settings.db_path).list_jobs()) == 1
    result = runner.invoke(cli, ["export", job["id"], "-o", str(settings.jobs_dir / job["id"] / "new.txt")])
    assert result.exit_code != 0 and "preserve its checkpoints" in result.output


def test_waiting_for_another_worker_does_not_fail_the_job(runner, local_job):
    media, _, _, _, _ = local_job
    calls = [0]

    def busy_then_process(**kwargs):
        calls[0] += 1
        return 75 if calls[0] == 1 else run_worker(**kwargs)

    with patch("echoscript.cli.run_worker", side_effect=busy_then_process) as worker:
        result = runner.invoke(cli, ["transcribe", str(media), "--quiet"])
    assert result.exit_code == 0, result.output
    assert worker.call_count == 2


def test_failure_and_ctrl_c_preserve_a_resumable_job(runner, local_job):
    media, settings, pipeline, models, _ = local_job
    pipeline.run.side_effect = RuntimeError("Model unavailable")
    result = runner.invoke(cli, ["transcribe", str(media)])
    assert result.exit_code == 1 and "Model unavailable" in result.output and "echoscript resume" in result.output
    [failed] = JobStore(settings.db_path).list_jobs()
    assert failed["status"] == "failed"
    pipeline.run.side_effect = KeyboardInterrupt()
    result = runner.invoke(cli, ["resume", failed["id"]])
    assert result.exit_code == 1 and "Stop requested" in result.output
    assert JobStore(settings.db_path).get_job(failed["id"])["status"] == "cancelled"
    pipeline.save_partial_result.assert_called_once()
    assert models.unload_all.call_count >= 2


@pytest.mark.parametrize("failure", ["recognition", "alignment", "validation"])
def test_incomplete_exports_are_saved_but_return_failure(runner, local_job, tmp_path, failure):
    media, _, _, _, transcript = local_job
    if failure == "recognition":
        transcript.metadata["integrity"]["complete"] = False
    elif failure == "alignment":
        transcript.metadata["alignment"]["status"] = "partial"
    else:
        transcript.segments.append(TranscriptSegment(2, 2.01, "too fast"))
        transcript.text += " too fast"
    destination = tmp_path / "result.srt"
    result = runner.invoke(cli, ["transcribe", str(media), "-o", str(destination)])
    assert result.exit_code == 1 and "incomplete" in result.output
    assert destination.is_file() and "Transcribed text" in destination.read_text()


@pytest.mark.parametrize("job_id", ["../bad", "0" * 32])
def test_unknown_job_ids_are_readable_errors(runner, local_job, job_id):
    result = runner.invoke(cli, ["export", job_id])
    assert result.exit_code == 1 and "Error:" in result.output


def test_list_no_options(runner):
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 1
    assert "Please specify either --models or --languages" in result.output


def test_list_models_and_language_filters(runner):
    result = runner.invoke(cli, ["list", "--models"])
    assert result.exit_code == 0
    assert "Qwen/Qwen3-ASR-1.7B" in result.output and "tiny" in result.output and "turbo" in result.output
    result = runner.invoke(cli, ["list", "--languages", "--backend", "qwen"])
    assert result.exit_code == 0
    assert "en: English" in result.output and "fr: French" in result.output and "zh: Chinese" in result.output
    assert "ja-zh" in result.output and "Whisper" not in result.output


def test_help_and_version_do_not_load_models(runner):
    with patch("echoscript.worker.worker.ModelManager") as models:
        for args in (["--help"], ["transcribe", "--help"], ["--version"]):
            result = runner.invoke(cli, args)
            assert result.exit_code == 0, result.output
        models.assert_not_called()
