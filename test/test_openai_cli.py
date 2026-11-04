import pytest

from echoscript.cli import cli
from test_cli import local_job, runner


@pytest.mark.parametrize("command, arguments", [
    (["transcribe"], ["--backend", "openai"]),
    (["transcribe"], ["--model", "gpt-transcribe"]),
    (["-a"], ["--model-name", "gpt-transcribe"]),
])
def test_openai_cli_uses_saved_worker_flow(runner, local_job, monkeypatch, command, arguments):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    media, _, pipeline, _, _ = local_job

    result = runner.invoke(cli, [*command, str(media), *arguments, "--language", "English", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "Transcribed text\n"
    options = pipeline.run.call_args.args[0]["options"]
    assert (options["asr_backend"], options["asr_model"], options["language"], options["timestamps"]) == (
        "openai", "gpt-transcribe", "en", False,
    )


def test_openai_all_formats_export_only_text_and_json(runner, local_job, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    media, _, _, _, _ = local_job
    destination = tmp_path / "exports"

    result = runner.invoke(cli, [
        "transcribe", str(media), "--model", "gpt-transcribe",
        "-f", "all", "--output-dir", str(destination), "--quiet",
    ])

    assert result.exit_code == 0, result.output
    assert {path.name for path in destination.iterdir()} == {"result.txt", "result.json"}


@pytest.mark.parametrize("arguments, message", [
    (["--timestamps"], "use --no-timestamps or choose a local backend"),
    (["--diarize"], "gpt-transcribe"),
    (["-f", "srt"], "with a local backend"),
    (["--language", "zz"], "Unsupported openai language"),
])
def test_unsupported_openai_options_fail_before_work(runner, local_job, arguments, message):
    media, _, pipeline, models, _ = local_job

    result = runner.invoke(cli, ["transcribe", str(media), "--backend", "openai", *arguments])

    assert result.exit_code != 0 and message in result.output
    pipeline.run.assert_not_called()
    models.unload_all.assert_not_called()
