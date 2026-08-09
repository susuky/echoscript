from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from echoscript.config import Settings
from echoscript.media import download_public_url, extract_audio, probe_duration
from echoscript.pipeline.fusion import assign_speakers
from echoscript.pipeline.normalize import normalize_transcript
from echoscript.render import render_transcript
from echoscript.schema import JobOptions, Transcript

if TYPE_CHECKING:
    from echoscript.worker.model_manager import ModelManager


StageCallback = Callable[[str], None]


class TranscriptionPipeline:
    def __init__(self, settings: Settings, models: ModelManager):
        self.settings = settings
        self.models = models

    def run(self, job: dict[str, Any], *, on_stage: StageCallback | None = None) -> Path:
        callback = on_stage or (lambda _stage: None)
        options = JobOptions.from_dict(job["options"])
        job_dir = self.settings.jobs_dir / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)

        callback("ingesting")
        media_path = self._resolve_media(job, job_dir)

        callback("extracting_audio")
        max_duration = self.settings.max_media_duration_seconds
        source_duration = probe_duration(media_path, ffprobe_bin=self.settings.ffprobe_bin)
        if max_duration > 0 and source_duration is not None and source_duration > max_duration:
            raise ValueError(
                f"Media duration {source_duration:.3f}s exceeds configured limit of {max_duration}s"
            )
        audio_path = job_dir / "audio.wav"
        if not audio_path.exists():
            extract_audio(
                media_path,
                audio_path,
                ffmpeg_bin=self.settings.ffmpeg_bin,
                max_duration_seconds=max_duration if max_duration > 0 else None,
            )
        duration = probe_duration(audio_path, ffprobe_bin=self.settings.ffprobe_bin)
        if max_duration > 0:
            if duration is None:
                raise RuntimeError("Could not verify extracted audio duration")
            # Reaching the hard ffmpeg boundary means an unprobeable source may
            # have been truncated; fail rather than silently transcribing a prefix.
            if duration >= max_duration - 0.05:
                raise ValueError(
                    f"Extracted audio reached configured duration limit of {max_duration}s"
                )

        callback("transcribing")
        transcriber = self.models.get_transcriber(options)
        transcript = transcriber.transcribe(
            audio_path,
            language=options.language,
            context=options.context,
            timestamps=options.timestamps,
            duration=duration,
        )
        self._write_json(job_dir / "asr.json", transcript.to_dict())

        if options.diarize:
            callback("diarizing")
            if self.settings.release_between_stages:
                self.models.drop_asr()
            diarizer = self.models.get_diarizer(options)
            turns = diarizer.diarize(
                audio_path,
                min_speakers=options.min_speakers,
                max_speakers=options.max_speakers,
            )
            self._write_json(job_dir / "diarization.json", [turn.__dict__ for turn in turns])
            transcript = assign_speakers(transcript, turns)

        callback("normalizing")
        transcript = normalize_transcript(transcript, options.zh_script)

        callback("rendering")
        output_dir = job_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        canonical_path = output_dir / "result.json"
        self._write_json(canonical_path, transcript.to_dict())
        for fmt in dict.fromkeys(options.output_formats):
            if fmt == "json":
                continue
            (output_dir / f"result.{fmt}").write_text(render_transcript(transcript, fmt), encoding="utf-8")
        return canonical_path

    def _resolve_media(self, job: dict[str, Any], job_dir: Path) -> Path:
        if job["source_type"] == "url":
            existing = [path for path in job_dir.glob("source.*") if path.is_file()]
            return (
                existing[0]
                if existing
                else download_public_url(
                    job["source_value"],
                    job_dir,
                    max_bytes=self.settings.max_remote_download_bytes,
                )
            )
        if job.get("media_path"):
            path = Path(job["media_path"])
            if path.exists():
                return path
        raise FileNotFoundError("Job media is missing")

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
