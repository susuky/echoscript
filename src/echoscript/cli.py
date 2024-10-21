
import click
import sys
from echoscript import audio2text


@click.command()
@click.option('-a', '--audio', help='The audio file to transcribe', type=click.Path(exists=True))
@click.option('-m', '--model-name', help='The name of the Whisper model to use', default='base')
@click.option('-f', '--fmt', help='The format of the audio. Supported formats {`json`, `srt`, `None`}', default=None)
@click.option('-l', '--language', help='The language of the audio', default=None)
# @click.option('-o', '--output-dir', help='The output directory', default=None)
# @click.option('-n', '--output-filename', help='The filename of the output audio text', default=None)
@click.option('-v', '--verbose/--no-verbose', help='Verbose mode', is_flag=True, default=True)
def main(audio, 
         model_name, 
         fmt, 
         language,
         filename=None,
         verbose=True):
    
    text = audio2text(audio, model_name, fmt, language)

    # if filename is not None:
    #     with open(filename, 'w') as f:
    #         f.write(text)

    if verbose:
        print(text)
    return 0

     

if __name__ == '__main__':
    sys.exit(main())  # pragma: no cover

