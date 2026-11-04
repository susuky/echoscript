from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

import httpx

from echoscript.schema import Transcript
from .base import Transcriber
from .prompts import bounded_text


class OpenAITranscriptionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OpenAITranscriber(Transcriber):
    def __init__(self, model_name: str = "gpt-transcribe", *, api_key: str | None = None):
        if model_name != "gpt-transcribe":
            raise ValueError("OpenAI 轉錄模型請使用 gpt-transcribe。")
        key = (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
        if not key:
            raise OpenAITranscriptionError("請先儲存 OpenAI API key，或設定 OPENAI_API_KEY。")
        if not key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in key):
            raise OpenAITranscriptionError("OpenAI API key 格式錯誤，請重新輸入。")
        self.model_name = model_name
        self._api_key = key

    @property
    def model_key(self) -> str:
        return f"openai:{self.model_name}"

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        context: str = "",
        glossary: str = "",
        previous_text: str = "",
        chunk_seconds: float = 60,
        condition_on_previous_text: bool = True,
        context_token_budget: int | None = None,
        glossary_token_budget: int | None = None,
        timestamps: bool = False,
        duration: float | None = None,
    ) -> Transcript:
        if timestamps:
            raise ValueError("gpt-transcribe 不提供時間戳，請關閉時間戳後重試。")
        path = Path(audio_path)
        if path.stat().st_size > 25_000_000:
            raise OpenAITranscriptionError("OpenAI 音訊分段上限為 25 MB，請縮短分段長度後重試。")
        for budget in (context_token_budget, glossary_token_budget):
            if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or not 0 <= budget <= 8192):
                raise ValueError("提示詞上限須為 0 到 8192 的整數。")
        context, context_info = bounded_text(context, 512 if context_token_budget is None else context_token_budget)
        glossary, glossary_info = bounded_text(glossary, 256 if glossary_token_budget is None else glossary_token_budget)
        previous_text, previous_info = bounded_text(
            previous_text if condition_on_previous_text else "", 256, tail=True,
        )
        prompt = "\n".join(part for part in (context, previous_text) if part)
        keywords = list(dict.fromkeys(term.strip() for term in re.split(r"[,，\n]+", glossary) if term.strip()))
        if any("<" in term or ">" in term or "\r" in term for term in keywords):
            raise ValueError("OpenAI 術語不可包含 <、> 或換行字元，請修正後重試。")
        languages = (language or "").strip().lower().replace("_", "-")
        data: dict[str, str | list[str]] = {"model": self.model_name, "response_format": "json"}
        if languages not in {"", "auto", "none"}:
            data["languages[]"] = [code.strip() for code in languages.split(",") if code.strip()]
        if prompt:
            data["prompt"] = prompt
        if keywords:
            data["keywords[]"] = keywords
        try:
            with path.open("rb") as audio:
                response = httpx.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data,
                    files={"file": (path.name, audio, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
                    timeout=httpx.Timeout(300, connect=15),
                )
        except httpx.TimeoutException:
            raise OpenAITranscriptionError("OpenAI 轉錄逾時，請縮短分段長度後重試。") from None
        except httpx.RequestError:
            raise OpenAITranscriptionError("無法連線至 OpenAI，請檢查網路後重試。") from None
        if response.status_code == 401:
            raise OpenAITranscriptionError("OpenAI API key 無效（401），請重新輸入。", status_code=401)
        if response.status_code == 403:
            raise OpenAITranscriptionError("OpenAI API key 無法使用此模型（403），請確認專案權限。", status_code=403)
        if response.status_code == 429:
            raise OpenAITranscriptionError("OpenAI 額度不足或請求過於頻繁（429），請檢查帳務與用量後重試。", status_code=429)
        if response.status_code >= 500:
            raise OpenAITranscriptionError("OpenAI 轉錄服務暫時無法使用，請稍後重試。", status_code=response.status_code)
        if not response.is_success:
            raise OpenAITranscriptionError("OpenAI 未接受轉錄請求，請檢查語言與音訊格式後重試。", status_code=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise OpenAITranscriptionError("OpenAI 回傳的轉錄格式不完整，請重試。") from None
        detected = payload.get("languages", []) if isinstance(payload, dict) else None
        if (not isinstance(payload, dict) or not isinstance(payload.get("text"), str)
                or not isinstance(detected, list)
                or any(not isinstance(item, dict) or not isinstance(item.get("code"), str) or not item["code"] for item in detected)):
            raise OpenAITranscriptionError("OpenAI 回傳的轉錄格式不完整，請重試。")
        codes = list(dict.fromkeys(item["code"] for item in detected))
        return Transcript(
            text=payload["text"], raw_text=payload["text"], language=",".join(codes) or None,
            duration=duration,
            metadata={
                "backend": "openai", "model": self.model_name, "timestamps": False,
                "alignment": {"status": "disabled"}, "languages": codes,
                "condition_on_previous_text": condition_on_previous_text,
                "prompt": {"context": context_info, "glossary": glossary_info, "previous_text": previous_info},
            },
        )
