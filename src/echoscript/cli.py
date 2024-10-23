
import click
import sys

from echoscript import audio2text, Audio2Text
from echoscript.gradio_app import TranscriptionApp


@click.group(invoke_without_command=True)
@click.option('-a', '--audio', help='The audio file to transcribe', type=click.Path(exists=True))
@click.option('-m', '--model-name', help='The name of the Whisper model to use', default='base')
@click.option('-f', '--fmt', help='The format of the audio. Supported formats {`json`, `srt`, `None`}', default=None)
@click.option('-l', '--language', help='The language of the audio', default=None)
@click.option('-o', '--filename', help='The filename of the output file', default=None)
@click.option('-v', '--verbose/--no-verbose', help='Verbose mode', is_flag=True, default=True)
@click.pass_context
def cli(ctx, audio, model_name, fmt, language, filename, verbose):
    '''
    CLI tool for audio transcription and model/language listing.
    '''
    if ctx.invoked_subcommand is None:
        if audio is None:
            click.echo('Please provide an audio file. Use --help for more information.')
            sys.exit(1)
        transcribe(audio, model_name, fmt, language, filename, verbose)


def transcribe(audio, 
               model_name, 
               fmt, 
               language,
               filename=None,
               verbose=True):
    
    text = audio2text(audio, model_name, fmt, language)

    if filename is not None:
        with open(filename, 'w') as f:
            f.write(text)

    if verbose:
        click.echo(text)
    return 0


@cli.command()
@click.option('--models', is_flag=True)
@click.option('--languages', '--langs', is_flag=True)
def list(models, languages):

    if models:
        text = '\n'.join(
            f'\t- {model}'
            for model in Audio2Text.available_models
        )
        text = f'Available models:\n{text}'
        click.echo_via_pager(text)

    if languages:
        text = '\n'.join(
            f'\t- {code}: {language}'
            for code, language in Audio2Text.available_languages.items()
        )
        text = f'Available languages:\n{text}'
        click.echo_via_pager(text)

    if not models and not languages:
        click.echo('Please specify either --models or --languages')
        sys.exit(1)


@cli.command()
@click.option('--port', type=int, default=7860)
@click.option('--server_name', type=str, default='0.0.0.0')
@click.option('--share2pub/--no-share2pub', default=False)
def serve(port, server_name, share2pub):
    app = TranscriptionApp()
    app.launch(port, server_name, share2pub)

     
if __name__ == '__main__':
    sys.exit(cli())  # pragma: no cover

