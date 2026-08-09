from unittest.mock import MagicMock

from echoscript.web import _refresh_web_job, create_web_app


def _components_by_label(app):
    components = app.get_config_file()["components"]
    return {
        component.get("props", {}).get("label"): component
        for component in components
        if component.get("props", {}).get("label")
    }


def test_web_form_uses_dropdowns_and_hides_internal_job_fields():
    app = create_web_app(MagicMock())
    components = app.get_config_file()["components"]
    labels = _components_by_label(app)

    assert labels["Model"]["type"] == "dropdown"
    assert labels["Language"]["type"] == "dropdown"
    assert labels["Chinese output"]["type"] == "dropdown"
    assert "Job ID" not in labels
    assert "Job" not in labels
    assert not any(component["type"] == "code" for component in components)


def test_backend_change_replaces_model_dropdown_choices():
    app = create_web_app(MagicMock())
    model_callback = next(block.fn for block in app.fns.values() if block.name == "model_dropdown")

    updated = model_callback("faster-whisper")
    choices = [value for _label, value in updated.choices]

    assert updated.value == "large-v3-turbo"
    assert "large-v3" in choices
    assert not any(choice.startswith("Qwen/") for choice in choices)


def test_refresh_displays_completed_transcript_and_downloads(monkeypatch, tmp_path):
    controller = MagicMock()
    controller.job.return_value = {"id": "job-1", "status": "done", "stage": "completed"}
    result_txt = tmp_path / "results" / "result.txt"
    result_srt = tmp_path / "results" / "result.srt"
    result_txt.parent.mkdir()
    result_txt.write_text("transcribed text", encoding="utf-8")
    result_srt.write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr("echoscript.web.tempfile.gettempdir", lambda: str(tmp_path / "temp"))
    controller.result.return_value = ("transcribed text", [str(result_txt), str(result_srt)])
    cache = {}

    status, transcript, downloads = _refresh_web_job(controller, cache, "job-1")

    assert status.startswith("**Status:** Done")
    assert transcript == "transcribed text"
    assert downloads == [
        str(tmp_path / "temp" / "echoscript-web" / "job-1" / "result.txt"),
        str(tmp_path / "temp" / "echoscript-web" / "job-1" / "result.srt"),
    ]
    assert cache["job-1"] == (transcript, downloads)


def test_refresh_missing_job_returns_status_instead_of_raising():
    controller = MagicMock()
    controller.job.side_effect = KeyError("job-1")

    status, transcript, downloads = _refresh_web_job(controller, {}, "job-1")

    assert "Job not found" in status
    assert transcript == ""
    assert downloads == []


def test_refresh_result_read_error_returns_status_instead_of_raising():
    controller = MagicMock()
    controller.job.return_value = {"id": "job-1", "status": "done"}
    controller.result.side_effect = OSError("result disappeared")

    status, transcript, downloads = _refresh_web_job(controller, {}, "job-1")

    assert "Could not load result" in status
    assert transcript == ""
    assert downloads == []
