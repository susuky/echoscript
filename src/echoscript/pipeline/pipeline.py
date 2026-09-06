from __future__ import annotations

import math
import time
import uuid
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from echoscript.config import Settings
from echoscript.media import download_public_url, extract_audio, probe_duration
from echoscript.pipeline.fusion import assign_speakers
from echoscript.pipeline.normalize import normalize_transcript
from echoscript.render import render_transcript, validate_subtitles
from echoscript.schema import JobOptions, Transcript, TranscriptSegment, SpeakerTurn
from .checkpoints import write_json, load_json, load_edits, plan_chunks, extract_chunk, progress

if TYPE_CHECKING:
    from echoscript.worker.model_manager import ModelManager


StageCallback = Callable[[str], None]


class TranscriptionPipeline:
    def __init__(self, settings: Settings, models: ModelManager):
        self.settings = settings
        self.models = models

    def run(self, job: dict[str, Any], *, on_stage: StageCallback | None = None) -> Path:
        from echoscript.storage.jobs import JobCancelled
        try:
            return self._run(job, on_stage=on_stage)
        except JobCancelled:
            self.save_partial_result(job)
            raise

    def _run(self, job: dict[str, Any], *, on_stage: StageCallback | None = None) -> Path:
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

        manifest = plan_chunks(audio_path, job_dir, options, lambda: callback("planning"))
        transcript = self._transcribe_chunks(audio_path, job_dir, manifest, options, callback)
        self._write_json(job_dir / "asr.json", transcript.to_dict())

        if options.diarize:
            callback("diarizing")
            if self.settings.release_between_stages:
                self.models.drop_asr()
            saved_turns = job_dir / "diarization.json"
            if saved_turns.exists():
                turns = [SpeakerTurn(**turn) for turn in load_json(saved_turns)]
            else:
                diarizer = self.models.get_diarizer(options)
                turns = diarizer.diarize(
                    audio_path,
                    min_speakers=options.min_speakers,
                    max_speakers=options.max_speakers,
                )
                self._write_json(saved_turns, [turn.__dict__ for turn in turns])
            transcript = assign_speakers(transcript, turns)

        callback("normalizing")
        transcript = normalize_transcript(transcript, options.zh_script)

        callback("rendering")
        edits = load_edits(job_dir)
        apply_edits(transcript, edits)
        result = publish_result(job_dir, transcript, options.output_formats, edits=edits)
        if transcript.metadata["progress"]["recognized"] == 0:
            raise RuntimeError("No audio chunks completed recognition; saved gaps are available for retry")
        return result

    def save_partial_result(self, job: dict[str, Any]) -> Path | None:
        """Make completed checkpoints reviewable when cancellation interrupts a pass."""
        job_dir = self.settings.jobs_dir / job["id"]
        manifest_path = job_dir / "chunks.json"
        if not manifest_path.exists():
            return None
        manifest = load_json(manifest_path)
        options = JobOptions.from_dict(job["options"])
        segments, texts, languages = [], [], []
        for chunk in manifest["chunks"]:
            saved = job_dir / "chunks" / f"{chunk['id']}.aligned.json"
            if not saved.exists():
                saved = job_dir / "chunks" / f"{chunk['id']}.asr.json"
            if saved.exists():
                part = Transcript.from_dict(load_json(saved))
            else:
                part = Transcript(text="", segments=[TranscriptSegment(
                    0, chunk["end"]-chunk["start"], "", alignment="unavailable",
                    diagnostics={"issues": ["asr_failed" if chunk["asr_status"] == "failed" else "pending"]})])
            if not part.segments:
                part.segments = [TranscriptSegment(0, chunk["end"]-chunk["start"], part.text, alignment="unavailable")]
            for index, segment in enumerate(part.segments):
                segment.id = f"{chunk['id']}:{index}"
                segment.start += chunk["start"]
                segment.end += chunk["start"]
                segment.diagnostics["chunk_id"] = chunk["id"]
                for word in segment.words:
                    word.start += chunk["start"]
                    word.end += chunk["start"]
                segments.append(segment)
            texts.append(part.text)
            if part.language and part.language not in languages:
                languages.append(part.language)
        transcript = Transcript(text="\n".join(texts), duration=manifest["duration"], segments=segments,
                                language=",".join(languages) or options.language, metadata={
                                    "backend": options.asr_backend, "model": options.asr_model,
                                    "progress": progress(manifest), "chunks": manifest["chunks"],
                                    "alignment": {"status": ("partial" if any(s.alignment == "available" for s in segments)
                                                               else "unavailable") if options.timestamps else "disabled"},
                                    "integrity": {"complete": False, "planned_seconds": manifest["duration"],
                                                  "recognized_seconds": progress(manifest)["processed_seconds"]}})
        normalize_transcript(transcript, options.zh_script)
        edits = load_edits(job_dir)
        apply_edits(transcript, edits)
        return publish_result(job_dir, transcript, options.output_formats, edits=edits)

    def _transcribe_chunks(self, audio_path, job_dir, manifest, options, callback):
        parts = []
        text_parts = []
        languages = []
        previous_text = ""
        transcriber = None
        released_aligner = False
        recognized_parts = []
        chunks_dir = job_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True)
        for chunk in manifest["chunks"]:
            callback("transcribing")
            chunk_id = chunk["id"]
            wav = chunks_dir / f"{chunk_id}.wav"
            asr_path = chunks_dir / f"{chunk_id}.asr.json"
            chunk_duration = chunk["end"] - chunk["start"]
            if not wav.exists():
                chunk["diagnostics"].update(extract_chunk(audio_path, wav, chunk))
            started = time.monotonic()
            try:
                if asr_path.exists():
                    part = Transcript.from_dict(load_json(asr_path))
                else:
                    if transcriber is None:
                        transcriber = self.models.get_transcriber(options)
                    if not released_aligner and hasattr(transcriber, "release_aligner"):
                        transcriber.release_aligner()
                        released_aligner = True
                    part = transcriber.transcribe(
                        wav, language=options.language, context=options.context,
                        glossary=options.glossary, previous_text=previous_text,
                        chunk_seconds=options.chunk_seconds or (60 if options.asr_backend == "qwen" else 300),
                        condition_on_previous_text=options.condition_on_previous_text,
                        context_token_budget=options.context_token_budget,
                        glossary_token_budget=options.glossary_token_budget,
                        timestamps=options.timestamps and not hasattr(transcriber, "align"),
                        duration=chunk_duration,
                    )
                    chunk["asr_seconds"] = time.monotonic() - started
                    self._write_json(asr_path, part.to_dict())
                chunk["asr_status"] = "done"
                chunk.pop("asr_error", None)
            except Exception as exc:
                chunk["asr_status"] = "failed"
                chunk["asr_error"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
                chunk["alignment_status"] = "blocked"
                part = Transcript(text="", duration=chunk_duration, segments=[TranscriptSegment(
                    0, chunk_duration, "", alignment="unavailable", diagnostics={"issues": ["asr_failed"]})])
            recognized_parts.append(part)
            previous_text = part.text if chunk["asr_status"] == "done" else ""
            self._write_json(job_dir / "chunks.json", manifest)
            callback("transcribing")

        # Finish every recognition checkpoint before loading the aligner. Its
        # GPU residency must not turn a later ASR chunk into an out-of-memory failure.
        previous_text = ""
        for chunk, part in zip(manifest["chunks"], recognized_parts):
            chunk_id = chunk["id"]
            wav = chunks_dir / f"{chunk_id}.wav"
            aligned_path = chunks_dir / f"{chunk_id}.aligned.json"
            chunk_duration = chunk["end"] - chunk["start"]
            callback("aligning" if options.timestamps else "transcribing")
            if chunk["asr_status"] == "done" and options.timestamps:
                try:
                    alignment_complete = False
                    if aligned_path.exists():
                        part = Transcript.from_dict(load_json(aligned_path))
                        alignment_complete = part.metadata.get("alignment", {}).get("status", "available") not in {"unavailable", "partial"}
                    if not alignment_complete:
                        if transcriber is None:
                            transcriber = self.models.get_transcriber(options)
                        if hasattr(transcriber, "align"):
                            started = time.monotonic()
                            part = transcriber.align(wav, part, duration=chunk_duration)
                            chunk["alignment_seconds"] = time.monotonic() - started
                        self._write_json(aligned_path, part.to_dict())
                    alignment = part.metadata.get("alignment", {}).get("status", "available")
                    chunk["alignment_status"] = "failed" if alignment in {"unavailable", "partial"} and part.text.strip() else "done"
                    chunk.pop("alignment_error", None)
                except Exception as exc:
                    chunk["alignment_status"] = "failed"
                    chunk["alignment_error"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
                    part.segments = [TranscriptSegment(0, chunk_duration, part.text, alignment="unavailable",
                                                       diagnostics={"issues": ["alignment_failed"]})]
            elif not options.timestamps:
                chunk["alignment_status"] = "disabled"
            if not part.segments:
                part.segments = [TranscriptSegment(0, chunk_duration, part.text, alignment="unavailable")]
            # Older checkpoints can gain acoustic diagnostics without running
            # recognition or alignment again. The extracted WAV is replaced atomically.
            if "activity_intervals" not in chunk["diagnostics"]:
                chunk["diagnostics"].update(extract_chunk(audio_path, wav, chunk))
            issues = chunk["diagnostics"].setdefault("issues", [])
            issues.clear()
            chunk["diagnostics"].pop("uncovered_activity", None)
            if chunk["alignment_status"] == "done" and part.text.strip():
                coverage = []
                for segment in part.segments:
                    if segment.alignment != "available":
                        continue
                    for item in segment.words or [segment]:
                        if (item.text.strip() and math.isfinite(item.start) and math.isfinite(item.end)
                                and 0 <= item.start < item.end <= chunk_duration + 0.1):
                            coverage.append((max(0, item.start - 0.3), min(chunk_duration, item.end + 0.3)))
                covered = []
                for start, end in sorted(coverage):
                    if covered and start <= covered[-1][1]:
                        covered[-1][1] = max(covered[-1][1], end)
                    else:
                        covered.append([start, end])
                uncovered = []
                index = 0
                if covered:
                    for activity in chunk["diagnostics"]["activity_intervals"]:
                        cursor, end = activity["start"], activity["end"]
                        while index < len(covered) and covered[index][1] <= cursor:
                            index += 1
                        scan = index
                        while scan < len(covered) and covered[scan][0] < end:
                            start, stop = covered[scan]
                            if start - cursor > 1:
                                uncovered.append({"start": round(cursor, 4), "end": round(start, 4)})
                            cursor = max(cursor, stop)
                            if cursor >= end:
                                break
                            scan += 1
                        if end - cursor > 1:
                            uncovered.append({"start": round(cursor, 4), "end": round(end, 4)})
                if uncovered:
                    chunk["diagnostics"]["uncovered_activity"] = {
                        "intervals": uncovered, "time_reference": "chunk_relative",
                        "padding_seconds": 0.3, "minimum_gap_seconds": 1,
                        "interpretation": "possible_omission_or_non_speech_audio",
                    }
                    issues.append("possible_omission")
            if not part.text.strip():
                issues.append("no_speech" if chunk["diagnostics"].get("digital_silence") else "possible_omission")
            if part.text.strip() and previous_text.strip() == part.text.strip():
                issues.append("possible_repetition")
            if chunk["alignment_status"] == "failed":
                issues.append("alignment_failed")
            # Signals stay hypotheses: energy alone cannot identify speech or a missed sentence.
            normalized = re.sub(r"\s+", "", part.text)
            if len(normalized) > 30 and any(normalized.endswith(normalized[-n:] * 3) for n in range(2, min(60, len(normalized)//3)+1)):
                issues.append("possible_repetition")
            for segment in part.segments:
                detail = segment.diagnostics
                generated, limit = detail.get("generated_tokens"), detail.get("generation_limit")
                if (detail.get("finish_reason") in {"length", "max_tokens"}
                        or isinstance(generated, int) and isinstance(limit, int) and generated >= limit):
                    issues.append("possible_truncation")
                if isinstance(detail.get("compression_ratio"), (float, int)) and detail["compression_ratio"] > 2.4:
                    issues.append("possible_repetition")
                if isinstance(detail.get("avg_logprob"), (float, int)) and detail["avg_logprob"] < -1:
                    issues.append("low_confidence")
            chunk["model_diagnostics"] = part.metadata
            for index, segment in enumerate(part.segments):
                segment.id = f"{chunk_id}:{index}"
                segment.start += chunk["start"]
                segment.end += chunk["start"]
                segment.raw_text = segment.raw_text if segment.raw_text is not None else segment.text
                segment.diagnostics.update({"chunk_id": chunk_id, "issues": list(dict.fromkeys(
                    segment.diagnostics.get("issues", []) + issues))})
                for word in segment.words:
                    word.start += chunk["start"]
                    word.end += chunk["start"]
            parts.extend(part.segments)
            text_parts.append(part.text)
            if part.language and part.language not in languages:
                languages.append(part.language)
            previous_text = part.text if chunk["asr_status"] == "done" else ""
            self._write_json(job_dir / "chunks.json", manifest)
            callback("transcribing")
        failed = any(c["asr_status"] == "failed" or c["alignment_status"] == "failed" for c in manifest["chunks"])
        text = "\n".join(text_parts)
        return Transcript(text=text, raw_text=text, segments=parts, language=",".join(languages) or options.language,
                          duration=manifest["duration"], metadata={
                              "backend": options.asr_backend, "model": options.asr_model,
                              "alignment": {"status": "partial" if failed else "available" if options.timestamps else "disabled"},
                              "progress": progress(manifest), "chunks": manifest["chunks"],
                              "integrity": {"complete": not any(c["asr_status"] == "failed" for c in manifest["chunks"]),
                                            "planned_seconds": manifest["duration"],
                                            "recognized_seconds": sum(c["end"] - c["start"] for c in manifest["chunks"] if c["asr_status"] == "done"),
                                            "range_coverage": "complete"},
                          })

    def _resolve_media(self, job: dict[str, Any], job_dir: Path) -> Path:
        if job["source_type"] == "url":
            existing = [
                path for path in job_dir.iterdir()
                if (path.name.startswith("source.") or path.name.startswith("source-"))
                and path.is_file() and not path.is_symlink()
                and path.suffix.lower() not in {".part", ".ytdl", ".json", ".tmp", ".temp"}
            ]
            return (
                existing[0]
                if existing
                else download_public_url(
                    job["source_value"],
                    job_dir,
                    max_bytes=self.settings.max_remote_download_bytes,
                    browser=getattr(self.settings, "youtube_browser", None),
                    profile=getattr(self.settings, "youtube_browser_profile", None),
                    keyring=getattr(self.settings, "youtube_browser_keyring", None),
                )
            )
        if job.get("media_path"):
            path = Path(job["media_path"])
            if path.exists():
                return path
        raise FileNotFoundError("Job media is missing")

    _write_json = staticmethod(write_json)


def apply_edits(transcript: Transcript, edits: dict) -> None:
    if not edits:
        return
    from echoscript.schema import rebuild_transcript_text
    applied = set()
    unapplied = []
    for segment in transcript.segments:
        edit = edits.get(segment.id)
        if edit:
            applied.add(segment.id)
            if edit.get("raw_text") != segment.raw_text:
                unapplied.append({"segment_id": segment.id, "reason": "source_text_changed", **edit})
                continue
            segment.text = edit["text"]
            segment.words = []
            segment.diagnostics["edited"] = True
    unapplied.extend({"segment_id": key, "reason": "segment_not_found", **edit}
                     for key, edit in edits.items() if key not in applied)
    if unapplied:
        transcript.metadata["unapplied_edits"] = unapplied
    rebuild_transcript_text(transcript)


def publish_result(job_dir: Path, transcript: Transcript, formats: list[str], *, edits: dict | None = None) -> Path:
    """Publish one immutable revision; readers resolve the pointer once per request."""
    valid = validate_subtitles(transcript)
    revision = uuid.uuid4().hex
    transcript.metadata["revision"] = revision
    output_dir = job_dir / "revisions" / revision
    output_dir.mkdir(parents=True)
    for fmt in dict.fromkeys(["json", *formats]):
        if fmt in {"srt", "vtt"} and not valid:
            continue
        content = render_transcript(transcript, fmt)
        (output_dir / f"result.{fmt}").write_text(content, encoding="utf-8")
    pointer = job_dir / "current.json"
    write_json(pointer, {"revision": revision, "edits": load_edits(job_dir) if edits is None else edits})
    return output_dir / "result.json"
