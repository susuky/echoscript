from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from echoscript.audio2text import Audio2Text, audio2text
from echoscript.media import download_with_browser_cookies
from echoscript.worker import run_worker


@click.group(invoke_without_command=True)
@click.option("-a", "--audio", type=click.Path(exists=True), default=None, help="Audio file to transcribe locally")
@click.option("-m", "--model-name", default="base", show_default=True)
@click.option("-f", "--fmt", default=None, help="Output format: json, txt, srt, or vtt")
@click.option("-l", "--language", "--lang", default=None)
@click.option("-o", "--filename", type=click.Path(), default=None, help="Write local output to this file")
@click.option("-v", "--verbose/--no-verbose", default=True, help="Print local transcription output")
@click.pass_context
def cli(
    ctx: click.Context,
    audio: str | None,
    model_name: str,
    fmt: str | None,
    language: str | None,
    filename: str | None,
    verbose: bool,
) -> None:
    """echoscript: local audio/video transcription."""
    if ctx.invoked_subcommand is not None:
        return
    if audio is None:
        click.echo("Please provide an audio file. Use echoscript --help for more information.")
        ctx.exit(1)

    result = audio2text(audio, model_name=model_name, fmt=fmt, language=language)
    rendered = (
        json.dumps(result, ensure_ascii=False, indent=2)
        if isinstance(result, (dict, list))
        else str(result)
    )
    if filename is not None:
        Path(filename).write_text(rendered, encoding="utf-8")
    if verbose:
        click.echo(rendered)


@cli.command(name="list")
@click.option("--models", is_flag=True)
@click.option("--languages", "--langs", is_flag=True)
def list_legacy(models: bool, languages: bool) -> None:
    """List models and languages supported by the synchronous interface."""
    if not models and not languages:
        click.echo("Please specify either --models or --languages")
        raise click.exceptions.Exit(1)
    if models:
        click.echo(
            "Available models:\n"
            + "\n".join(f"\t- {model}" for model in Audio2Text.available_models)
        )
    if languages:
        click.echo(
            "Available languages:\n"
            + "\n".join(
                f"\t- {code}: {name}"
                for code, name in Audio2Text.available_languages.items()
            )
        )


@cli.command()
@click.option("--verbose", is_flag=True)
def worker(verbose: bool) -> None:
    """Process one queued job in an isolated local GPU worker."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    raise SystemExit(run_worker())


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
    type=click.IntRange(min=1),
    default=2 * 1024**3,
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
    """Download login-required media locally for later Web upload."""
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
    """Run the local Gradio application."""
    from echoscript.web import run_web

    run_web()


if __name__ == "__main__":
    cli()
