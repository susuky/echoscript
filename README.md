# echoscript

echoscript is an audio transcription tool based on the Whisper model, providing a simple and user-friendly command-line interface (CLI) for audio transcription tasks.

## Features

- Audio transcription: Convert audio files to text
- Support for multiple Whisper models
- Multiple output formats (plain text, JSON, SRT)
- Multi-language transcription support
- Command-line interface (CLI) operation

## Installation

```bash
pip install echoscript
```

## Usage

### Basic Usage

```bash
echoscript -a path/to/audio/file.mp3
```

### Advanced Options

```bash
echoscript -a path/to/audio/file.mp3 -m medium -f srt -l en -o output.srt -v
```

- `-a`, `--audio`: Path to the audio file for transcription
- `-m`, `--model-name`: Name of the Whisper model to use (default is 'base')
- `-f`, `--fmt`: Output format, supports `json`, `srt`, or None (plain text)
- `-l`, `--language`: Language of the audio
- `-o`, `--filename`: Output filename
- `-v`, `--verbose`: Verbose mode, outputs transcription result to console

### List Available Models and Languages

```bash
echoscript list --models
echoscript list --languages
```

## Development Plans

Future features planned:

- Speaker Diarization
- Additional audio processing and analysis features


## License

[MIT License](LICENSE)