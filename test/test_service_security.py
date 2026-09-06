from unittest.mock import patch

import pytest

from echoscript.config import Settings
from echoscript.worker.worker import _acquire_worker_lock


def test_settings_uses_huggingface_cli_token_when_env_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

    with patch("huggingface_hub.get_token", return_value="cached-token"):
        settings = Settings.from_env()

    assert settings.hf_token == "cached-token"


def test_explicit_huggingface_token_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "environment-token")

    with patch("huggingface_hub.get_token", return_value="cached-token") as get_token:
        settings = Settings.from_env()

    assert settings.hf_token == "environment-token"
    get_token.assert_not_called()


def test_file_sizes_are_unlimited_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.max_upload_bytes == 0
    assert settings.max_remote_download_bytes == 0
    assert settings.max_media_duration_seconds == 43_200
    assert settings.job_retention_days == 30
    assert settings.worker_lock_path == (tmp_path / "worker.lock").resolve()


def test_resource_limit_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("ECHOSCRIPT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHOSCRIPT_MAX_UPLOAD_BYTES", "123")
    monkeypatch.setenv("ECHOSCRIPT_MAX_REMOTE_DOWNLOAD_BYTES", "456")
    monkeypatch.setenv("ECHOSCRIPT_MAX_MEDIA_DURATION_SECONDS", "789")
    monkeypatch.setenv("ECHOSCRIPT_JOB_RETENTION_DAYS", "0")
    monkeypatch.setenv("ECHOSCRIPT_WORKER_LOCK_PATH", str(tmp_path / "custom.lock"))
    settings = Settings.from_env()
    assert settings.max_upload_bytes == 123
    assert settings.max_remote_download_bytes == 456
    assert settings.max_media_duration_seconds == 789
    assert settings.job_retention_days == 0
    assert settings.worker_lock_path == (tmp_path / "custom.lock").resolve()


def test_worker_lock_rejects_a_second_worker(tmp_path):
    first = _acquire_worker_lock(tmp_path / "worker.lock")
    try:
        with pytest.raises(RuntimeError, match="Another echoscript worker"):
            _acquire_worker_lock(tmp_path / "worker.lock")
    finally:
        first.close()
