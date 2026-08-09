from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from echoscript.config import Settings
from echoscript.pipeline.pipeline import TranscriptionPipeline
from echoscript.schema import JobOptions, Transcript
from echoscript.storage import JobStore
from echoscript.worker.model_manager import ModelManager


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ECHOSCRIPT_RUN_INTEGRATION") != "1",
        reason="set ECHOSCRIPT_RUN_INTEGRATION=1 to run real model acceptance",
    ),
]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def test_real_media_pipeline(tmp_path):
    media_value = os.getenv("ECHOSCRIPT_E2E_MEDIA")
    if not media_value:
        pytest.fail("ECHOSCRIPT_E2E_MEDIA must point to a local audio/video file")
    media_path = Path(media_value).expanduser().resolve()
    if not media_path.is_file():
        pytest.fail(f"ECHOSCRIPT_E2E_MEDIA is not a file: {media_path}")

    backend = os.getenv("ECHOSCRIPT_E2E_BACKEND", "faster-whisper")
    default_model = (
        "Qwen/Qwen3-ASR-1.7B" if backend == "qwen" else "large-v3-turbo"
    )
    model = os.getenv("ECHOSCRIPT_E2E_MODEL", default_model)
    language = os.getenv("ECHOSCRIPT_E2E_LANGUAGE") or None
    timestamps = _env_bool("ECHOSCRIPT_E2E_TIMESTAMPS", True)
    diarize = _env_bool("ECHOSCRIPT_E2E_DIARIZE", False)
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if diarize and not hf_token:
        pytest.skip("real diarization acceptance requires HF_TOKEN/HUGGINGFACE_TOKEN")

    minimum_duration = float(os.getenv("ECHOSCRIPT_E2E_MIN_DURATION_SECONDS", "0"))
    options = JobOptions(
        asr_backend=backend,
        asr_model=model,
        language=language,
        timestamps=timestamps,
        diarize=diarize,
        device=os.getenv("ECHOSCRIPT_E2E_DEVICE", "cuda"),
        compute_type=os.getenv("ECHOSCRIPT_E2E_COMPUTE_TYPE", "float16"),
    )
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "jobs.sqlite3",
        jobs_dir=tmp_path / "jobs",
        max_media_duration_seconds=int(
            os.getenv("ECHOSCRIPT_E2E_MAX_DURATION_SECONDS", "43200")
        ),
        worker_lock_path=tmp_path / "worker.lock",
        hf_token=hf_token,
    )
    settings.ensure_directories()
    store = JobStore(settings.db_path)
    job = store.create_job(
        source_type="upload",
        source_value=media_path.name,
        media_path=str(media_path),
        options=options,
    )
    claimed = store.claim_next_job()
    assert claimed is not None and claimed["id"] == job["id"]

    models = ModelManager(hf_token=hf_token)
    try:
        pipeline = TranscriptionPipeline(settings, models)
        result_path = pipeline.run(
            claimed,
            on_stage=lambda stage: store.set_stage(job["id"], stage),
        )
        store.complete(job["id"], str(result_path))
    finally:
        models.unload_all()

    transcript = Transcript.from_dict(json.loads(result_path.read_text(encoding="utf-8")))
    assert transcript.text.strip(), "ASR returned empty text"
    if minimum_duration > 0:
        assert transcript.duration is not None
        assert transcript.duration >= minimum_duration
    if timestamps:
        assert transcript.segments, "timestamps requested but no segments were rendered"
    if diarize:
        assert transcript.speakers, "diarization requested but no speakers were attributed"
        assert any(segment.speaker for segment in transcript.segments)

    output_dir = result_path.parent
    required_formats = ("json", "txt", "srt", "vtt") if timestamps else ("json", "txt")
    for fmt in required_formats:
        rendered = output_dir / f"result.{fmt}"
        assert rendered.is_file(), f"missing {fmt} output"
        assert rendered.stat().st_size > 0, f"empty {fmt} output"
