from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import ClientDisconnect

from echoscript.schema import JobOptions
from echoscript.pipeline.checkpoints import job_lock, load_json, load_edits, write_json, progress

if TYPE_CHECKING:
    from echoscript.web import LocalJobController

log = logging.getLogger("echoscript.api")
MODELS = {
    "qwen": [("Qwen/Qwen3-ASR-1.7B", "完整模型"), ("Qwen/Qwen3-ASR-0.6B", "輕量模型")],
    "faster-whisper": [("large-v3", "完整模型"), ("large-v3-turbo", "快速模型"),
                       ("medium", "中型模型"), ("small", "小型模型"), ("base", "基本模型"), ("tiny", "最小模型")],
}
LANGUAGES = [("auto", "混合語言／自動辨識"), ("ja", "日文"), ("ja-zh", "日中混合課程"), ("zh", "中文"),
             ("en", "英文"), ("ko", "韓文"), ("yue", "粵語"), ("fr", "法文"),
             ("de", "德文"), ("es", "西班牙文"), ("pt", "葡萄牙文"), ("it", "義大利文"),
             ("ru", "俄文"), ("th", "泰文"), ("vi", "越南文"), ("id", "印尼文"),
             ("ms", "馬來文"), ("ar", "阿拉伯文"), ("hi", "印地文"), ("tr", "土耳其文"),
             ("nl", "荷蘭文"), ("pl", "波蘭文"), ("sv", "瑞典文"), ("da", "丹麥文"),
             ("fi", "芬蘭文"), ("el", "希臘文"), ("hu", "匈牙利文"), ("ro", "羅馬尼亞文"),
             ("cs", "捷克文"), ("tl", "菲律賓文"), ("fa", "波斯文"), ("mk", "馬其頓文")]
FILE_TYPES = {"txt": "text/plain", "srt": "application/x-subrip", "vtt": "text/vtt", "json": "application/json"}


def _available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


class WebOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asr_backend: Literal["qwen", "faster-whisper"] = "qwen"
    asr_model: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=20)
    timestamps: bool = True
    diarize: bool = False
    zh_script: Literal["tw", "twp"] | None = "tw"
    context: str = Field(default="", max_length=8000)
    glossary: str = Field(default="", max_length=8000)
    condition_on_previous_text: bool = True
    context_token_budget: int | None = Field(default=None, ge=0, le=8192)
    glossary_token_budget: int | None = Field(default=None, ge=0, le=8192)
    chunk_seconds: float | None = Field(default=None, ge=5, le=1800)
    chunk_strategy: Literal["energy", "fixed"] = "energy"
    min_speakers: int | None = Field(default=None, ge=1, le=50)
    max_speakers: int | None = Field(default=None, ge=1, le=50)

    def job_options(self) -> JobOptions:
        values = self.model_dump(exclude_none=True)
        values["zh_script"] = self.zh_script
        if self.language in {None, "", "auto"}:
            values["language"] = None
        elif self.language == "ja-zh":
            values["language"] = None
            values["context"] = "日中雙語課程，日本講師與台灣翻譯輪流說話。\n" + self.context.strip()
        elif self.language not in {code for code, _ in LANGUAGES}:
            raise ValueError("Unsupported language")
        options = JobOptions.from_dict(values)
        if options.asr_model not in {model for model, _ in MODELS[options.asr_backend]}:
            raise ValueError("Unsupported model")
        return options


class RetryChunks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_ids: list[str] = Field(default_factory=list, max_length=5000)
    stage: Literal["asr", "alignment", "failed"] = "failed"
    revision: str | None = Field(default=None, max_length=32)


class EditSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)
    revision: str = Field(min_length=32, max_length=32)


class NewJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["upload", "url"]
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, max_length=4096)
    options: WebOptions = Field(default_factory=WebOptions)


def _error_message(error: str) -> str:
    value = error.lower()
    if "members-only" in value or "join this channel" in value:
        return "這支影片限頻道會員觀看。請在已登入會員帳號的電腦下載，再上傳音影片檔案。"
    if "private video" in value or "sign in if you've been granted access" in value:
        return "這是私人影片。請在已獲觀看權限並登入 YouTube 的電腦下載，再上傳音影片檔案。"
    if "unable to download video data" in value or ("[youtube]" in value and "403" in value):
        return "YouTube 未允許此次下載。請在能正常播放影片的電腦下載，再上傳檔案。"
    if "size limit" in value or "too large" in value:
        return "檔案超過大小上限，請縮小檔案後重新上傳。"
    if "duration" in value and "limit" in value:
        return "音影片超過長度上限，請分成較短的檔案後再試。"
    if "out of memory" in value:
        return "此次轉錄所需資源不足，請選用較輕量的模型或稍後再試。"
    if "hf_token" in value or "gated" in value or "401" in value or "403" in value:
        return "目前無法使用這項辨識功能，請聯絡管理者確認存取設定。"
    if "non-public" in value or "local and" in value:
        return "這個連結無法使用，請改用公開音影片連結或直接上傳檔案。"
    if "direct http" in value or "requested format" in value or "compressed http responses" in value:
        return "此連結無法直接下載，請先下載音影片，再上傳檔案。"
    if "unsupported" in value:
        return "目前不支援這個選項或檔案格式，請選擇其他設定後再試。"
    if "timestamp" in value and "diarization" in value:
        return "區分說話者需要開啟字幕時間。"
    if "upload" in value:
        return "上傳未完成，請重新選擇檔案後再試一次。"
    return "處理失敗，請確認音影片可正常播放，或改用本機檔案重新上傳。"


def _visible_job(job: dict) -> dict:
    if job.get("error"):
        return {**job, "error": _error_message(str(job["error"]))}
    return job


def _job(controller: LocalJobController, job_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "找不到這份轉錄。")
    try:
        return controller.job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "找不到這份轉錄，可能已超過保留期限。") from exc


async def _new_job_body(request: Request) -> NewJob:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 64 * 1024:
            raise HTTPException(413, "設定內容過長，請縮短專有名詞提示。")
    try:
        return NewJob.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(422, "請檢查檔名、連結與轉錄設定後再試。") from exc


def create_api(controller: LocalJobController) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await asyncio.to_thread(controller.start)
        try:
            yield
        finally:
            await asyncio.to_thread(controller.stop)

    app = FastAPI(title="echoscript", lifespan=lifespan)
    app.state.controller = controller

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/config")
    def configuration():
        return {
            "languages": [{"code": code, "label": label} for code, label in LANGUAGES],
            "backends": [{"id": backend, "label": label,
                          "available": _available(module),
                          "models": [{"id": model, "label": name} for model, name in MODELS[backend]]}
                         for backend, label, module in [("qwen", "多語轉錄", "qwen_asr"),
                                                        ("faster-whisper", "通用轉錄", "faster_whisper")]],
            "max_upload_bytes": controller.settings.max_upload_bytes,
            "diarization_available": bool(controller.settings.hf_token)
                and _available("pyannote.audio"),
            "default_options": WebOptions().model_dump() | {"asr_model": "Qwen/Qwen3-ASR-1.7B"},
        }

    @app.get("/api/jobs")
    def jobs(limit: int = 50):
        return {"jobs": [_visible_job(controller._public_job(job)) for job in controller.store.list_jobs(limit)]}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return _visible_job(_job(controller, job_id))

    @app.get("/api/jobs/{job_id}/chunks")
    def chunks(job_id: str):
        _job(controller, job_id)
        path = controller.settings.jobs_dir / job_id / "chunks.json"
        if not path.is_file():
            return {"chunks": [], "progress": None}
        manifest = load_json(path)
        return {"chunks": [{key: chunk[key] for key in ("id", "start", "end", "asr_status", "alignment_status", "diagnostics")}
                           for chunk in manifest["chunks"]], "progress": progress(manifest)}

    @app.get("/api/jobs/{job_id}/audio")
    def audio(job_id: str):
        _job(controller, job_id)
        directory = (controller.settings.jobs_dir / job_id).resolve()
        path = directory / "audio.wav"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "原音尚未準備好。")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        _job(controller, job_id)
        if not controller.store.cancel(job_id):
            raise HTTPException(409, "這份轉錄已停止處理。")
        return _visible_job(controller.job(job_id))

    @app.post("/api/jobs/{job_id}/resume")
    def resume(job_id: str, body: RetryChunks):
        _job(controller, job_id)
        directory = controller.settings.jobs_dir / job_id
        with job_lock(directory):
            current = _job(controller, job_id)
            if current["status"] not in {"done", "failed", "cancelled"}:
                raise HTTPException(409, "請先停止處理，再重試片段。")
            pointer = directory / "current.json"
            if body.revision and (not pointer.is_file() or load_json(pointer)["revision"] != body.revision):
                raise HTTPException(409, "內容已更新，請重新載入後再試。")
            manifest_path = directory / "chunks.json"
            if body.chunk_ids and not manifest_path.is_file():
                raise HTTPException(404, "找不到指定片段。")
            if manifest_path.is_file():
                manifest = load_json(manifest_path)
                known = {chunk["id"] for chunk in manifest["chunks"]}
                if set(body.chunk_ids) - known:
                    raise HTTPException(404, "找不到指定片段。")
                edits_path = directory / "edits.json"
                edits = load_edits(directory)
                for chunk in manifest["chunks"]:
                    if body.chunk_ids and chunk["id"] not in body.chunk_ids:
                        continue
                    stage = body.stage
                    if stage == "failed":
                        stage = "asr" if chunk["asr_status"] == "failed" else "alignment" if chunk["alignment_status"] == "failed" else ""
                    if not stage:
                        continue
                    if stage == "asr":
                        (directory / "chunks" / f"{chunk['id']}.asr.json").unlink(missing_ok=True)
                        (directory / "chunks" / f"{chunk['id']}.aligned.json").unlink(missing_ok=True)
                        chunk["asr_status"] = "pending"
                        edits = {key: value for key, value in edits.items() if not key.startswith(chunk["id"] + ":")}
                    chunk["alignment_status"] = "pending"
                write_json(edits_path, edits)
                if pointer.is_file():
                    current_pointer = load_json(pointer)
                    current_pointer["edits"] = edits
                    write_json(pointer, current_pointer)
                write_json(manifest_path, manifest)
            if not controller.store.resume(job_id):
                raise HTTPException(409, "這份轉錄的狀態已變更。")
        controller._wake.set()
        return _visible_job(controller.job(job_id))

    @app.patch("/api/jobs/{job_id}/segments/{segment_id}")
    def edit_segment(job_id: str, segment_id: str, body: EditSegment):
        from echoscript.schema import Transcript, rebuild_transcript_text
        from echoscript.pipeline.pipeline import publish_result
        _job(controller, job_id)
        directory = controller.settings.jobs_dir / job_id
        with job_lock(directory):
            current = _job(controller, job_id)
            if current["status"] not in {"done", "failed", "cancelled"}:
                raise HTTPException(409, "請先停止處理，再修正文字。")
            _, paths = result_paths(job_id)
            canonical = next(Path(path) for path in paths if Path(path).suffix == ".json")
            transcript = Transcript.from_dict(load_json(canonical))
            if transcript.metadata.get("revision") != body.revision:
                raise HTTPException(409, "內容已更新，請重新載入後再儲存。")
            segment = next((seg for seg in transcript.segments if seg.id == segment_id), None)
            if segment is None:
                raise HTTPException(404, "找不到這個段落。")
            if not body.text.strip():
                raise HTTPException(422, "修正文字不可為空白。")
            segment.raw_text = segment.raw_text if segment.raw_text is not None else segment.text
            segment.text = body.text.strip()
            segment.words = []
            segment.diagnostics["edited"] = True
            edits_path = directory / "edits.json"
            edits = load_edits(directory)
            edits[segment.id] = {"text": segment.text, "raw_text": segment.raw_text, "updated_at": time.time()}
            rebuild_transcript_text(transcript)
            write_json(edits_path, edits)
            path = publish_result(directory, transcript, JobOptions.from_dict(current["options"]).output_formats, edits=edits)
            controller.store.set_result_path(job_id, str(path))
        return result(job_id)

    @app.post("/api/jobs", status_code=201, openapi_extra={
        "requestBody": {"required": True, "content": {"application/json": {
            "schema": {"type": "object"},
            "example": {"source_type": "url", "url": "https://www.youtube.com/watch?v=VIDEO_ID",
                        "options": {"language": "ja", "timestamps": True}},
        }}},
    })
    async def create_job(request: Request):
        body = await _new_job_body(request)
        try:
            options = body.options.job_options()
            module = "qwen_asr" if options.asr_backend == "qwen" else "faster_whisper"
            if not _available(module):
                raise HTTPException(400, "目前無法使用這個轉錄選項，請選擇其他模型。")
            if body.source_type == "upload":
                if not body.filename or not body.filename.strip() or "\x00" in body.filename:
                    raise HTTPException(400, "請先選擇音影片檔案。")
                result = await asyncio.to_thread(controller.begin_upload, body.filename, options)
            else:
                if not body.url:
                    raise HTTPException(400, "請輸入公開音影片連結。")
                result = await asyncio.to_thread(controller.submit_url, body.url.strip(), options)
            return _visible_job(result)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, _error_message(str(exc))) from exc

    @app.put("/api/jobs/{job_id}/media", openapi_extra={
        "requestBody": {"required": True, "content": {"application/octet-stream": {
            "schema": {"type": "string", "format": "binary"},
        }}},
    })
    async def upload(job_id: str, request: Request):
        current = _job(controller, job_id)
        if current["source_type"] != "upload" or current["status"] != "uploading":
            raise HTTPException(409, "這份檔案已上傳，請建立新的轉錄。")
        maximum = controller.settings.max_upload_bytes
        raw_length = request.headers.get("content-length")
        try:
            expected = int(raw_length) if raw_length is not None else None
        except ValueError as exc:
            raise HTTPException(400, "無法確認檔案大小，請重新上傳。") from exc
        directory = controller.settings.jobs_dir / job_id
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ("upload" + Path(current["source_value"]).suffix[:16])
        partial = directory / (destination.name + ".part")
        try:
            output = partial.open("xb")
        except FileExistsError as exc:
            raise HTTPException(409, "這份檔案正在上傳，請稍候。") from exc
        try:
            received = 0
            heartbeat = time.monotonic()
            with output:
                if expected is not None and (expected <= 0 or (maximum > 0 and expected > maximum)):
                    raise HTTPException(413 if maximum > 0 and expected > maximum else 400,
                                        "檔案超過大小上限。" if maximum > 0 and expected > maximum else "檔案沒有內容。")
                async for chunk in request.stream():
                    received += len(chunk)
                    if maximum > 0 and received > maximum:
                        raise HTTPException(413, "檔案超過大小上限。")
                    await asyncio.to_thread(output.write, chunk)
                    if time.monotonic() - heartbeat >= 30:
                        controller.store.set_stage(job_id, "uploading")
                        heartbeat = time.monotonic()
                if not received or (expected is not None and expected != received):
                    raise HTTPException(400, "檔案上傳不完整，請重新上傳。")
                await asyncio.to_thread(output.flush)
                await asyncio.to_thread(os.fsync, output.fileno())
            os.replace(partial, destination)
            controller.store.finish_upload(job_id, str(destination))
        except (Exception, asyncio.CancelledError) as exc:
            partial.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            controller.store.fail(job_id, "Upload exceeds configured size limit"
                                  if isinstance(exc, HTTPException) and exc.status_code == 413
                                  else "Upload failed: " + type(exc).__name__)
            if isinstance(exc, ClientDisconnect):
                return JSONResponse({"detail": "上傳已中斷，請重新上傳。"}, status_code=400)
            raise
        controller._wake.set()
        return _visible_job(controller.job(job_id))

    def result_paths(job_id: str):
        _job(controller, job_id)
        try:
            return controller.result(job_id)
        except ValueError as exc:
            raise HTTPException(409, "這份轉錄尚未完成。") from exc
        except (OSError, KeyError) as exc:
            raise HTTPException(404, "轉錄檔案已不存在，請重新提交。") from exc

    @app.get("/api/jobs/{job_id}/result")
    def result(job_id: str):
        text, paths = result_paths(job_id)
        canonical = next((Path(path) for path in paths if Path(path).suffix == ".json"), None)
        if canonical is None:
            raise HTTPException(404, "找不到轉錄結果，請重新提交。")
        try:
            transcript = json.loads(canonical.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "無法讀取這份結果，請重新提交。") from exc
        return {"text": text, "transcript": transcript,
                "files": [{"format": Path(path).suffix[1:], "url": f"/api/jobs/{job_id}/files/{Path(path).suffix[1:]}" + (f"?revision={transcript['metadata']['revision']}" if transcript.get("metadata", {}).get("revision") else "")}
                          for path in paths]}

    @app.get("/api/jobs/{job_id}/files/{format}")
    def download(job_id: str, format: str, revision: str | None = None):
        if format not in FILE_TYPES:
            raise HTTPException(404, "找不到這個下載格式。")
        _, paths = result_paths(job_id)
        if revision is not None:
            if not re.fullmatch(r"[a-f0-9]{32}", revision):
                raise HTTPException(404, "找不到這個版本。")
            directory = (controller.settings.jobs_dir / job_id / "revisions" / revision).resolve()
            if not directory.is_relative_to((controller.settings.jobs_dir / job_id).resolve()):
                raise HTTPException(404, "找不到這個版本。")
            file_path = directory / f"result.{format}"
            if file_path.is_symlink() or file_path.resolve().parent != directory:
                raise HTTPException(404, "找不到這個版本。")
            paths = [str(file_path)] if file_path.is_file() else []
        path = next((Path(path) for path in paths if Path(path).suffix == "." + format), None)
        if path is None:
            raise HTTPException(404, "這份轉錄沒有此格式的檔案。")
        return FileResponse(path, media_type=FILE_TYPES[format], filename=f"transcript.{format}")

    assets = Path(__file__).parent / "web_assets"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
