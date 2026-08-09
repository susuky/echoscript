from unittest.mock import MagicMock, patch

from echoscript.gradio_app import TranscriptionApp
from echoscript.web import run_web


def test_public_web_bind_does_not_require_auth(monkeypatch):
    monkeypatch.setenv("ECHOSCRIPT_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("ECHOSCRIPT_WEB_PORT", "8765")
    demo = MagicMock()
    controller = MagicMock()

    with (
        patch("echoscript.web.LocalJobController", return_value=controller),
        patch("echoscript.web.create_web_app", return_value=demo),
    ):
        run_web()

    controller.start.assert_called_once_with()
    controller.stop.assert_called_once_with()
    demo.launch.assert_called_once_with(
        server_name="0.0.0.0",
        server_port=8765,
        footer_links=[],
    )


def test_default_web_bind_is_public_without_auth(monkeypatch):
    monkeypatch.delenv("ECHOSCRIPT_WEB_HOST", raising=False)
    monkeypatch.delenv("ECHOSCRIPT_WEB_PORT", raising=False)
    demo = MagicMock()
    controller = MagicMock()

    with (
        patch("echoscript.web.LocalJobController", return_value=controller),
        patch("echoscript.web.create_web_app", return_value=demo),
    ):
        run_web()

    demo.launch.assert_called_once_with(
        server_name="0.0.0.0",
        server_port=7860,
        footer_links=[],
    )


def test_compat_launch_has_no_auth():
    controller = MagicMock()
    demo = MagicMock()
    app = TranscriptionApp(controller)

    with patch.object(app, "build_interface", return_value=demo):
        app.launch(server_name="0.0.0.0", server_port=8765)

    controller.start.assert_called_once_with()
    controller.stop.assert_called_once_with()
    demo.launch.assert_called_once_with(
        server_port=8765,
        server_name="0.0.0.0",
        share=False,
        debug=False,
        footer_links=[],
    )
