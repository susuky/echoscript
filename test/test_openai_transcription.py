import json

import httpx
import pytest

from echoscript.schema import JobOptions
from echoscript.transcription import OpenAITranscriber
from echoscript.transcription.openai import OpenAITranscriptionError
from echoscript.worker.model_manager import ModelManager


def _http_response(monkeypatch, status=200, payload=None, *, content=None):
    requests = []

    def respond(request):
        requests.append(request)
        request.read()
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=payload if payload is not None else {"text": "Hello.", "languages": []})

    def post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr("echoscript.transcription.openai.httpx.post", post)
    return requests


def test_openai_posts_audio_context_keywords_and_languages_without_fabricated_timestamps(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"RIFF audio content")
    requests = _http_response(monkeypatch, payload={
        "text": "今次使用 EchoScript。", "languages": [{"code": "yue"}, {"code": "en"}],
    })
    transcriber = OpenAITranscriber(api_key="sk-test-sentinel")
    transcript = transcriber.transcribe(
        audio, language="yue, en", context="產品會議", glossary="EchoScript, OpenAI\nEchoScript",
        previous_text="上段內容", duration=10,
    )
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer sk-test-sentinel"
    body = request.content
    assert b'name="file"; filename="speech.wav"' in body
    assert b"Content-Type: audio/x-wav" in body or b"Content-Type: audio/wav" in body
    assert b"RIFF audio content" in body
    assert b'name="model"\r\n\r\ngpt-transcribe' in body
    assert b'name="response_format"\r\n\r\njson' in body
    assert b'name="prompt"\r\n\r\n' + "產品會議\n上段內容".encode() in body
    assert body.count(b'name="keywords[]"') == 2
    assert b'name="keywords[]"\r\n\r\nEchoScript' in body
    assert b'name="languages[]"\r\n\r\nyue' in body
    assert b'name="languages[]"\r\n\r\nen' in body
    assert b'name="language"' not in body
    assert transcript.text == transcript.raw_text == "今次使用 EchoScript。"
    assert transcript.language == "yue,en"
    assert transcript.duration == 10
    assert transcript.segments == []
    assert transcript.metadata["timestamps"] is False
    assert "sk-test-sentinel" not in json.dumps(transcript.to_dict())
    assert "sk-test-sentinel" not in transcriber.model_key


def test_openai_disables_previous_context_and_respects_prompt_budgets(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"audio")
    requests = _http_response(monkeypatch)
    transcript = OpenAITranscriber(api_key="sk-test").transcribe(
        audio, context="abcdef", glossary="EchoScript", previous_text="private previous",
        context_token_budget=3, glossary_token_budget=0, condition_on_previous_text=False,
    )
    body = requests[0].content
    assert b'name="prompt"\r\n\r\nabc\r\n' in body
    assert b"private previous" not in body
    assert b'name="keywords[]"' not in body
    assert b'name="languages[]"' not in body
    assert transcript.language is None
    assert transcript.metadata["prompt"]["context"]["truncated"] is True


@pytest.mark.parametrize(("status", "message"), [
    (401, "API key 無效"), (403, "專案權限"), (429, "帳務與用量"),
    (503, "暫時無法使用"), (400, "語言與音訊格式"),
])
def test_openai_upstream_errors_are_actionable_and_do_not_leak_response_or_key(tmp_path, monkeypatch, status, message):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"audio")
    requests = _http_response(monkeypatch, status, {"error": {"message": "secret body sk-test-sentinel"}})
    with pytest.raises(OpenAITranscriptionError, match=message) as error:
        OpenAITranscriber(api_key="sk-test-sentinel").transcribe(audio)
    assert len(requests) == 1
    assert "secret body" not in str(error.value)
    assert "sk-test-sentinel" not in str(error.value)
    assert error.value.status_code == status


@pytest.mark.parametrize("payload", [[], {}, {"text": None}, {"text": "text", "languages": None},
                                      {"text": "text", "languages": ["en"]},
                                      {"text": "text", "languages": [{"code": 2}]}])
def test_openai_rejects_invalid_transcription_shapes(tmp_path, monkeypatch, payload):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"audio")
    _http_response(monkeypatch, payload=payload)
    with pytest.raises(RuntimeError, match="轉錄格式不完整"):
        OpenAITranscriber(api_key="sk-test").transcribe(audio)


def test_openai_rejects_invalid_json_and_sanitizes_network_error(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"audio")
    _http_response(monkeypatch, content=b"not json sk-test-sentinel")
    with pytest.raises(RuntimeError, match="轉錄格式不完整"):
        OpenAITranscriber(api_key="sk-test-sentinel").transcribe(audio)

    def fail(*args, **kwargs):
        raise httpx.ConnectError("sk-test-sentinel")

    monkeypatch.setattr("echoscript.transcription.openai.httpx.post", fail)
    with pytest.raises(RuntimeError, match="無法連線至 OpenAI") as error:
        OpenAITranscriber(api_key="sk-test-sentinel").transcribe(audio)
    assert "sk-test-sentinel" not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_openai_rejects_missing_key_unsupported_timestamps_and_oversized_audio(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAITranscriptionError, match="請先儲存 OpenAI API key"):
        OpenAITranscriber()
    audio = tmp_path / "speech.wav"
    with audio.open("wb") as handle:
        handle.truncate(25_000_001)
    transcriber = OpenAITranscriber(api_key="sk-test")
    with pytest.raises(ValueError, match="不提供時間戳"):
        transcriber.transcribe(audio, timestamps=True)
    with pytest.raises(OpenAITranscriptionError, match="25 MB"):
        transcriber.transcribe(audio)


def test_openai_job_options_default_model_and_reject_unsupported_features():
    options = JobOptions.from_dict({"asr_backend": "openai"})
    assert options.asr_model == "gpt-transcribe"
    assert options.timestamps is False
    assert JobOptions.from_dict(options.to_dict()) == options
    with pytest.raises(ValueError, match="不提供時間戳"):
        JobOptions.from_dict({"asr_backend": "openai", "timestamps": True})
    with pytest.raises(ValueError, match="不提供說話者分離"):
        JobOptions.from_dict({"asr_backend": "openai", "diarize": True})
    with pytest.raises(ValueError, match="600 秒"):
        JobOptions.from_dict({"asr_backend": "openai", "chunk_seconds": 601})


def test_openai_model_manager_refreshes_credentials_for_cached_workers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("echoscript.worker.model_manager._best_effort_cuda_cleanup", lambda: None)
    saved = {"key": "sk-first"}
    models = ModelManager(openai_api_key=lambda: saved["key"])
    options = JobOptions.from_dict({"asr_backend": "openai"})
    first = models.get_transcriber(options)
    assert isinstance(first, OpenAITranscriber)
    assert models.get_transcriber(options) is first
    saved["key"] = "sk-second"
    second = models.get_transcriber(options)
    assert second is not first
    assert second._api_key == "sk-second"
    assert "sk-second" not in models._asr_key
    saved["key"] = None
    with pytest.raises(OpenAITranscriptionError, match="請先儲存 OpenAI API key"):
        models.get_transcriber(options)
    assert models._asr is None


def test_openai_model_manager_refreshes_environment_key(monkeypatch):
    monkeypatch.setattr("echoscript.worker.model_manager._best_effort_cuda_cleanup", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-first")
    models = ModelManager()
    options = JobOptions.from_dict({"asr_backend": "openai"})
    first = models.get_transcriber(options)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-second")
    second = models.get_transcriber(options)
    assert second is not first
    assert second._api_key == "sk-second"
