from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

import click

from echoscript.audio2text import Audio2Text
from echoscript.media import download_with_browser_cookies
from echoscript.pipeline.checkpoints import job_lock
from echoscript.schema import JobOptions
from echoscript.transcription.qwen import _LANGUAGE_MAP, _normalize_language
from echoscript.web import LocalJobController
from echoscript.worker import run_worker


FORMATS = ("txt", "srt", "vtt", "json")
BACKENDS = ("qwen", "faster-whisper")


def output_options(command):
    for option in reversed([
        click.option("-f", "--format", "--fmt", "fmt", type=click.Choice([*FORMATS, "all"]),
                     multiple=True, help="Output format; repeat for several formats, or use all. Default: txt (or the output file extension)."),
        click.option("-o", "--output", "--filename", "filename", type=click.Path(dir_okay=False, path_type=Path),
                     help="Write one format to this file."),
        click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path),
                     help="Write result.<format> files to this directory."),
        click.option("--overwrite", is_flag=True, help="Replace existing export files."),
        click.option("-v", "--verbose/--no-verbose", default=True, help="Print a single-format transcript to stdout."),
        click.option("-q", "--quiet", is_flag=True, help="Hide job status messages on stderr."),
    ]):
        command = option(command)
    return command


def transcription_options(command):
    for option in reversed([
        click.option("-b", "--backend", "asr_backend", type=click.Choice(BACKENDS),
                     help="Default: qwen; a known Whisper model name selects faster-whisper."),
        click.option("-m", "--model", "--model-name", "asr_model",
                     help="Default: Qwen/Qwen3-ASR-1.7B, or large-v3-turbo for faster-whisper."),
        click.option("-l", "--language", "--lang", default=None,
                     help="Language code/name, auto, or ja-zh for a Japanese/Chinese lesson."),
        click.option("--context", default="", help="Recording background and topic."),
        click.option("--glossary", default="", help="Names, terminology, and preferred spellings."),
        click.option("--timestamps/--no-timestamps", default=True, show_default=True),
        click.option("--diarize/--no-diarize", default=False, show_default=True,
                     help="Identify speakers (requires the diarization extra and model access)."),
        click.option("--min-speakers", type=click.IntRange(min=1)),
        click.option("--max-speakers", type=click.IntRange(min=1)),
        click.option("--zh-script", type=click.Choice(["tw", "twp", "none"]), default="tw", show_default=True,
                     help="Chinese text conversion; none preserves the original script."),
        click.option("--condition-on-previous-text/--no-condition-on-previous-text", default=True, show_default=True),
        click.option("--context-token-budget", type=click.IntRange(0, 8192)),
        click.option("--glossary-token-budget", type=click.IntRange(0, 8192)),
        click.option("--chunk-seconds", type=click.FloatRange(5, 1800),
                     help="Maximum chunk length; default: 60 for Qwen, 300 for Whisper."),
        click.option("--chunk-strategy", type=click.Choice(["energy", "fixed"]), default="energy", show_default=True),
        click.option("--device", default="cuda", show_default=True, help="Inference device, e.g. cuda, cuda:0, or cpu."),
        click.option("--compute-type", default="float16", show_default=True, help="faster-whisper compute type, e.g. int8 for CPU."),
    ]):
        command = option(command)
    return output_options(command)


@click.group(invoke_without_command=True)
@click.version_option(package_name="echoscript")
@click.option("-a", "--audio", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Compatibility alias for transcribe FILE.")
@transcription_options
@click.pass_context
def cli(ctx: click.Context, audio: str | None, **options) -> None:
    """echoscript: local audio/video transcription."""
    if ctx.invoked_subcommand is not None:
        return
    if audio is None:
        click.echo("Please provide an audio file. Use echoscript --help for more information.")
        ctx.exit(1)

    model = options["asr_model"]
    if options["asr_backend"] is None and model is not None and not model.startswith("Qwen/"):
        options["asr_backend"] = "faster-whisper"
    ctx.invoke(transcribe, source=audio, **options)


def _job_options(values: dict) -> JobOptions:
    backend = values["asr_backend"] or (
        "faster-whisper" if (values["asr_model"] or "").removesuffix(".en") in Audio2Text.available_models else "qwen"
    )
    values["asr_backend"] = backend
    if values["asr_model"] is None:
        values.pop("asr_model")
    language = (values["language"] or "").strip().lower().replace("_", "-")
    if language in {"", "auto", "none"}:
        values["language"] = None
    elif language == "ja-zh":
        values["language"] = None
        values["context"] = "日中雙語課程，日本講師與台灣翻譯輪流說話。\n" + values["context"].strip()
    elif backend == "qwen":
        values["language"] = _normalize_language(language)
    else:
        code = Audio2Text._language_code(language)
        code = code.split("-")[0]
        if not Audio2Text.is_language_available(code):
            raise ValueError(f"Unsupported faster-whisper language: {language}")
        values["language"] = code
    if values["zh_script"] == "none":
        values["zh_script"] = None
    if not values["diarize"] and (values["min_speakers"] or values["max_speakers"]):
        raise ValueError("Speaker counts require --diarize")
    return JobOptions.from_dict(values)


def _output_plan(fmt, filename, output_dir, overwrite, *, timestamps=True, source=None):
    if filename is not None and output_dir is not None:
        raise click.UsageError("Use either --output or --output-dir")
    inferred = filename.suffix.lower().lstrip(".") if filename is not None else ""
    formats = list(dict.fromkeys(fmt)) if fmt else [inferred if inferred in FORMATS else "txt"]
    if "all" in formats:
        formats = list(FORMATS if timestamps else ("txt", "json"))
    if not timestamps and set(formats) & {"srt", "vtt"}:
        raise click.UsageError("SRT/VTT output requires --timestamps")
    if len(formats) > 1 and output_dir is None:
        raise click.UsageError("Multiple output formats require --output-dir")
    targets = (
        {fmt: output_dir / f"result.{fmt}" for fmt in formats} if output_dir is not None
        else {formats[0]: filename} if filename is not None else {}
    )
    for target in targets.values():
        if source is not None and (target.resolve() == source.resolve()
                                   or (target.exists() and target.samefile(source))):
            raise click.UsageError("The output file must not replace the input media")
        if target.exists() and (not overwrite or not target.is_file()):
            raise click.ClickException(f"Output already exists: {target}. Use --overwrite to replace a file.")
    return formats, targets


def _existing_job(controller: LocalJobController, job_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise click.ClickException("Invalid job ID; use echoscript jobs to list saved jobs")
    try:
        return controller.job(job_id)
    except KeyError as exc:
        raise click.ClickException(f"Job not found: {job_id}") from exc


def _wait_for_job(controller: LocalJobController, job_id: str, quiet: bool) -> None:
    if not quiet:
        click.echo(f"Job: {job_id}", err=True)
    last_stage = None
    try:
        while True:
            job = controller.job(job_id)
            if job["status"] in {"done", "failed", "cancelled"}:
                break
            if not quiet and job["stage"] != last_stage:
                click.echo(f"Status: {job['stage']}", err=True)
                last_stage = job["stage"]
            if job["status"] == "queued":
                # The existing worker lock serializes CLI and Web jobs. If another
                # worker owns it, wait for that worker or claim this job once idle.
                code = run_worker(job_id=job_id)
                if code not in {0, 75}:
                    raise click.ClickException(f"Worker exited with code {code}; job {job_id} is saved")
            time.sleep(0.25)
    except KeyboardInterrupt:
        controller.store.cancel(job_id)
        if not quiet:
            click.echo(f"Stop requested. Continue with: echoscript resume {job_id}", err=True)
        raise
    if job["status"] != "done":
        raise click.ClickException(
            f"Job {job_id} {job['status']}: {job.get('error') or 'processing stopped'}. "
            f"Continue with: echoscript resume {job_id}"
        )


def _export_job(controller, job_id, formats, targets, overwrite, verbose, quiet):
    _, files = controller.result(job_id)
    available = {Path(path).suffix.lstrip("."): Path(path) for path in files}
    missing = set(formats) - available.keys()
    job_dir = (controller.settings.jobs_dir / job_id).resolve()
    if any(target.resolve().is_relative_to(job_dir) for target in targets.values()):
        raise click.UsageError("Export outside the saved job directory to preserve its checkpoints and revisions")
    for fmt in formats:
        if fmt not in available:
            continue
        path = available[fmt]
        if fmt in targets:
            destination = targets[fmt]
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Publish complete exports, and check again after the potentially long
            # transcription so a newly created file is not silently replaced.
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
            try:
                shutil.copyfile(path, temporary)
                if overwrite:
                    os.replace(temporary, destination)
                else:
                    os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            if not quiet:
                click.echo(f"Saved: {destination.resolve()}", err=True)
        if verbose and len(formats) == 1:
            click.echo(path.read_text(encoding="utf-8"), nl=False)
    payload = json.loads(available["json"].read_text(encoding="utf-8")) if "json" in available else {}
    metadata = payload.get("metadata", {})
    incomplete = metadata.get("integrity", {}).get("complete") is False
    alignment_failed = metadata.get("alignment", {}).get("status") in {"partial", "unavailable"}
    subtitle_gaps = metadata.get("subtitles", {}).get("gaps")
    timestamps = JobOptions.from_dict(controller.job(job_id)["options"]).timestamps
    if (alignment_failed or (subtitle_gaps and timestamps)) and not quiet:
        click.echo("Some subtitle timing is unavailable; only validated segments are exported.", err=True)
    if missing or incomplete or (set(formats) & {"srt", "vtt"} and (alignment_failed or subtitle_gaps)):
        detail = f"Unavailable formats: {', '.join(sorted(missing))}. " if missing else ""
        raise click.ClickException(f"{detail}Result is incomplete; saved outputs are kept. Job: {job_id}")


@cli.command()
@click.argument("source")
@transcription_options
def transcribe(source: str, fmt, filename, output_dir, overwrite, verbose, quiet, **values) -> None:
    """Transcribe a local audio/video FILE or a public HTTP(S) URL."""
    try:
        options = _job_options(values)
        remote = source.lower().startswith(("https://", "http://"))
        media = None if remote else click.Path(exists=True, dir_okay=False, path_type=Path).convert(
            source, None, click.get_current_context()
        )
        formats, targets = _output_plan(fmt, filename, output_dir, overwrite, timestamps=options.timestamps, source=media)
        controller = LocalJobController()
        job = controller.submit_url(source, options) if remote else controller.submit_upload(media, options)
        _wait_for_job(controller, job["id"], quiet)
        _export_job(controller, job["id"], formats, targets, overwrite, verbose, quiet)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.argument("job_id", required=False)
@click.option("--limit", type=click.IntRange(1, 500), default=50, show_default=True)
def jobs(job_id: str | None, limit: int) -> None:
    """Print saved jobs, or one job's status and options, as JSON."""
    try:
        controller = LocalJobController()
        result = _existing_job(controller, job_id) if job_id else [
            controller._public_job(job) for job in controller.store.list_jobs(limit)
        ]
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.argument("job_id")
@output_options
def resume(job_id: str, fmt, filename, output_dir, overwrite, verbose, quiet) -> None:
    """Continue a saved job, reusing completed recognition checkpoints."""
    try:
        controller = LocalJobController()
        job = _existing_job(controller, job_id)
        options = JobOptions.from_dict(job["options"])
        formats, targets = _output_plan(fmt, filename, output_dir, overwrite, timestamps=options.timestamps)
        with job_lock(controller.settings.jobs_dir / job_id):
            if not controller.store.resume(job_id):
                raise click.ClickException("This job is still queued or running")
        _wait_for_job(controller, job_id, quiet)
        _export_job(controller, job_id, formats, targets, overwrite, verbose, quiet)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command(name="export")
@click.argument("job_id")
@output_options
def export_command(job_id: str, fmt, filename, output_dir, overwrite, verbose, quiet) -> None:
    """Export an existing result without loading a model."""
    try:
        controller = LocalJobController()
        job = _existing_job(controller, job_id)
        formats, targets = _output_plan(fmt, filename, output_dir, overwrite,
                                        timestamps=JobOptions.from_dict(job["options"]).timestamps)
        _export_job(controller, job_id, formats, targets, overwrite, verbose, quiet)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command(name="list")
@click.option("--models", is_flag=True)
@click.option("--languages", "--langs", is_flag=True)
@click.option("--backend", type=click.Choice(BACKENDS), default=None, help="Filter to one ASR backend.")
def list_options(models: bool, languages: bool, backend: str | None) -> None:
    """List model presets and backend-specific languages."""
    if not models and not languages:
        click.echo("Please specify either --models or --languages")
        raise click.exceptions.Exit(1)
    if models:
        from echoscript.api import MODELS

        if backend in {None, "qwen"}:
            click.echo("qwen models:\n" + "\n".join(f"\t- {model}" for model, _ in MODELS["qwen"]))
        if backend in {None, "faster-whisper"}:
            click.echo("faster-whisper models:\n" + "\n".join(f"\t- {model}" for model in Audio2Text.available_models))
    if languages:
        backends = BACKENDS if backend is None else (backend,)
        for selected in backends:
            available = _LANGUAGE_MAP if selected == "qwen" else Audio2Text.available_languages
            click.echo(f"{selected} languages:\n\t- auto: Automatic detection\n\t- ja-zh: Japanese/Chinese lesson\n"
                       + "\n".join(f"\t- {code}: {name}" for code, name in sorted(available.items())))


@cli.command()
@click.option("--verbose", is_flag=True)
@click.option("--job-id", default=None, help="Process this queued job first")
@click.option("--idle-timeout", type=click.FloatRange(min=0), default=0, show_default=True,
              help="Keep models available for more jobs for this many idle seconds")
def worker(verbose: bool, job_id: str | None, idle_timeout: float) -> None:
    """Process queued jobs in an isolated local GPU worker."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    raise SystemExit(run_worker(job_id=job_id, idle_timeout=idle_timeout))


@cli.command()
@click.argument("url")
@click.option("--browser", default="chrome", show_default=True, help="Browser containing the login cookies")
@click.option("--profile", default=None, help="Browser profile, for example 'Profile 2'")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
)
@click.option(
    "--yt-dlp-bin",
    default="yt-dlp",
    envvar="ECHOSCRIPT_YTDLP_BIN",
    show_default=True,
)
@click.option(
    "--max-download-bytes",
    type=click.IntRange(min=0),
    default=0,
    help="Download size limit in bytes; 0 means unlimited",
    envvar="ECHOSCRIPT_CLIENT_MAX_DOWNLOAD_BYTES",
    show_default=True,
)
def download(
    url: str,
    browser: str,
    profile: str | None,
    output_dir: Path,
    yt_dlp_bin: str,
    max_download_bytes: int,
) -> None:
    """Download login-required media using local browser cookies."""
    try:
        media = download_with_browser_cookies(
            url,
            output_dir.expanduser(),
            browser=browser,
            profile=profile,
            yt_dlp_bin=yt_dlp_bin,
            max_bytes=max_download_bytes,
        )
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(media.resolve()))


@cli.command(name="web")
def web_command() -> None:
    """Run the local transcription web application."""
    from echoscript.web import run_web

    run_web()


if __name__ == "__main__":
    cli()
