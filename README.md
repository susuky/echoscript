# echoscript

**English** | [繁體中文](README.zh-TW.md)

Turn recordings and videos into editable transcripts and subtitles, using local models or OpenAI.

- Upload audio/video files or paste a public YouTube link.
- Transcribe Japanese, Chinese, English, and mixed-language recordings, with hints for names and terminology.
- Listen, edit the text, and export TXT, SRT, VTT, or JSON.

The interface supports English and Traditional Chinese.

![EchoScript transcription workspace](docs/images/workspace-en.png)

## Install

You need **Linux** and [uv](https://docs.astral.sh/uv/getting-started/installation/). Local models also require an **NVIDIA GPU with its driver installed**. uv manages Python 3.11 for this project. The web interface is included; Node.js is not required.

Install Git and FFmpeg (Ubuntu / Debian):

```bash
sudo apt update
sudo apt install -y git ffmpeg
```

Clone and install:

```bash
git clone https://github.com/susuky/echoscript.git
cd echoscript
uv sync
```

After installation succeeds, create the local settings file:

```bash
test -e .env || cp .env.example .env
chmod 600 .env
```

## Start

From the project directory, run:

```bash
uv run --env-file .env --no-sync python serve.py
```

Open **[http://127.0.0.1:7860](http://127.0.0.1:7860)**, upload a recording, and start transcribing. The default model is Qwen3-ASR; its first use downloads model files and takes longer.

Press `Ctrl+C` to stop. Use the same command to start again.

The default settings allow access from your network and have no login. For local-only access, set `ECHOSCRIPT_WEB_HOST=127.0.0.1` in `.env` before starting.

## Use OpenAI

In **New transcript → Advanced settings → Transcription method**, select **OpenAI**, enter your **OpenAI API key**, and choose **Save**. You can then transcribe with `gpt-transcribe` without a local GPU. Audio is sent to OpenAI and billed to the key's account. This backend exports TXT and JSON; timestamps, subtitles, and speaker diarization are unavailable.

The saved key is shared by everyone using this server. It is stored in `ECHOSCRIPT_DATA_DIR/openai-api-key` with permissions `0600` and takes precedence over `OPENAI_API_KEY` in `.env`. The app does not save the key in browser storage or return it through the API.

## Command-line usage

After installation, transcribe the included sample without starting the web server:

```bash
uv run --env-file .env --no-sync echoscript transcribe This_is_an_example.mp3
```

The transcript appears in the terminal. Replace `This_is_an_example.mp3` with your audio/video file path.

To use OpenAI with the saved key or `OPENAI_API_KEY` in `.env`:

```bash
uv run --env-file .env --no-sync echoscript transcribe This_is_an_example.mp3 --model gpt-transcribe
```

For more CLI options, other models, speaker diarization, API usage, and deployment, see the [usage guide (Traditional Chinese)](docs/usage.md).
