# echoscript

Local, self-hosted audio/video transcription with a Gradio interface. The web process accepts
local uploads and coordinates GPU inference on the same machine without a separate remote-job
service or client.

## Architecture

```text
Browser
  |
  v
Gradio (serve.py)
  |
  +-- local job data / rendered outputs
  |
  +-- local GPU worker subprocess
        +-- ffmpeg normalization
        +-- Qwen3-ASR or faster-whisper
        +-- optional pyannote diarization
        +-- JSON / TXT / SRT / VTT
```

Models stay out of the long-lived Gradio process. Worker process exit remains the hard boundary
for releasing CUDA memory.

## Requirements

- Linux with Python 3.11
- ffmpeg and ffprobe
- [uv](https://docs.astral.sh/uv/)
- NVIDIA/CUDA for the default GPU configuration

This deployment uses the existing conda-base uv executable:

```bash
cd /home/nvidia/ping/echoscript
/home/nvidia/miniforge3/bin/uv sync --no-dev
```

This installs Gradio and the default Qwen backend. Add the optional backends only when needed:

```bash
/home/nvidia/miniforge3/bin/uv sync --no-dev --extra faster-whisper --extra diarization
```

For development tests, run `/home/nvidia/miniforge3/bin/uv sync`.

## Run

The repository-level entry point intentionally contains only a main guard and delegates to the
package web application:

```bash
cd /home/nvidia/ping/echoscript
/home/nvidia/miniforge3/bin/uv run --no-sync python serve.py
```

If you copied `.env`, load it for a manual run with
`/home/nvidia/miniforge3/bin/uv run --env-file .env --no-sync python serve.py`.

Open `http://HOST:7860`. The default bind address is `0.0.0.0` and the Gradio page does not
require a username or password. Keep the port on a trusted network or restrict it with a firewall.

## Configuration

Copy the deployment example and adjust its local paths or limits if needed:

```bash
cd /home/nvidia/ping/echoscript
cp deploy/systemd/echoscript.env.example .env
chmod 600 .env
```

Keep `.env` owned by `nvidia` and mode `0600` if it contains a Hugging Face token. The systemd
unit treats the file as optional.

Important settings:

- `ECHOSCRIPT_DATA_DIR`: local jobs, database, and rendered results.
- `ECHOSCRIPT_WEB_HOST` / `ECHOSCRIPT_WEB_PORT`: Gradio bind address and port.
- `HF_TOKEN`: optional override for pyannote. A token saved by `hf auth login` is detected
  automatically; you must still accept the pyannote model conditions on Hugging Face.
- `ECHOSCRIPT_RELEASE_BETWEEN_STAGES`: release ASR before diarization.
- `ECHOSCRIPT_MAX_UPLOAD_BYTES`: maximum accepted local upload size.
- `ECHOSCRIPT_MAX_MEDIA_DURATION_SECONDS`: default 12-hour duration ceiling.
- `ECHOSCRIPT_JOB_RETENTION_DAYS`: done/failed retention; non-positive disables cleanup.

## Transcribe local media

Upload an audio or video file in the Gradio page, choose the backend/model/language, optionally
enable speaker diarization, and submit. ffmpeg normalizes the source to mono 16 kHz PCM before
ASR. Results are rendered as JSON, plain text, SRT, and VTT.

Qwen defaults to `Qwen/Qwen3-ASR-1.7B` with its forced aligner when timestamps are enabled.
`faster-whisper` defaults to `large-v3-turbo`. Traditional Chinese output uses deterministic
OpenCC post-processing.

Diarization uses `pyannote/speaker-diarization-community-1`. If word timestamps are unavailable,
existing transcript segments are preserved and receive best-effort segment-level speaker
attribution instead of being discarded.

## Login-required or members-only media

Browser cookies are never stored by the service or automated in pytest. Download protected media
manually on the machine that owns the browser profile, then upload the resulting local file in
Gradio:

```bash
/home/nvidia/miniforge3/bin/uv run --no-sync echoscript download \
  'https://www.youtube.com/watch?v=...' \
  --browser chrome \
  --profile 'Profile 2' \
  --output-dir /home/nvidia/Downloads/echoscript \
  --max-download-bytes 2147483648
```

Use `--yt-dlp-bin` when yt-dlp is not on PATH. The command prints the retained media path; it
does not upload anything or retain the browser cookie database.

## Legacy local CLI

The synchronous compatibility interface remains available:

```bash
echoscript -a ./audio.mp3 -m tiny -f txt -l en -o transcript.txt
echoscript list --models
echoscript list --languages
```

`echoscript worker` is an internal command that processes one queued job and exits. Normal
operation should start `serve.py`; the Gradio process launches workers automatically.

## systemd

The included unit is intentionally specific to this checkout and user:

```text
User/Group:       nvidia
WorkingDirectory: /home/nvidia/ping/echoscript
EnvironmentFile: /home/nvidia/ping/echoscript/.env
uv executable:   /home/nvidia/miniforge3/bin/uv
stop behavior:   terminate the whole service control group, wait up to 30 seconds
```

Install and start it with:

```bash
cd /home/nvidia/ping/echoscript
cp deploy/systemd/echoscript.env.example .env
chmod 600 .env
sudo cp deploy/systemd/echoscript.service /etc/systemd/system/echoscript.service
sudo systemctl daemon-reload
sudo systemctl enable --now echoscript.service
sudo systemctl status echoscript.service
```

After editing `.env` or updating the environment, restart the service:

```bash
sudo systemctl restart echoscript.service
```

## Storage and limits

Each job is stored below `$ECHOSCRIPT_DATA_DIR/jobs/<job-id>/`, including normalized audio,
intermediate ASR/diarization JSON, and final outputs. Sources are probed before extraction,
ffmpeg receives a hard duration limit, and normalized PCM size/duration are verified afterward.
Done and failed jobs are cleaned during idle periods according to retention settings; active jobs
are not selected.

## Tests

The ordinary suite uses mocks and never downloads models:

```bash
/home/nvidia/miniforge3/bin/uv run --no-sync pytest
```

Real pipeline acceptance is opt-in and requires an existing local media file:

```bash
ECHOSCRIPT_RUN_INTEGRATION=1 \
ECHOSCRIPT_E2E_MEDIA=/absolute/path/to/sample.mp4 \
ECHOSCRIPT_E2E_BACKEND=faster-whisper \
ECHOSCRIPT_E2E_MODEL=large-v3-turbo \
ECHOSCRIPT_E2E_LANGUAGE=zh \
ECHOSCRIPT_E2E_MIN_DURATION_SECONDS=10 \
/home/nvidia/miniforge3/bin/uv run --no-sync pytest \
  -m integration test/integration/test_real_pipeline.py
```

For Qwen, set `ECHOSCRIPT_E2E_BACKEND=qwen` and
`ECHOSCRIPT_E2E_MODEL=Qwen/Qwen3-ASR-1.7B`. For real diarization, also set
`ECHOSCRIPT_E2E_DIARIZE=1` and `HF_TOKEN`. Login-required downloads remain a manual CLI
acceptance step so tests never read or persist browser cookies.

## License

MIT
