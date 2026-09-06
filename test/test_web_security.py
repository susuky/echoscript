from unittest.mock import MagicMock, patch

from echoscript.gradio_app import TranscriptionApp
from echoscript.web import run_web


def test_web_launch_uses_configured_host_and_port(monkeypatch):
    monkeypatch.setenv('ECHOSCRIPT_WEB_HOST', '127.0.0.1')
    monkeypatch.setenv('ECHOSCRIPT_WEB_PORT', '8765')
    app = MagicMock()
    with patch('echoscript.web.create_web_app', return_value=app), patch('uvicorn.run') as run:
        run_web()
    run.assert_called_once_with(app, host='127.0.0.1', port=8765)


def test_compat_wrapper_uses_same_api():
    controller = MagicMock()
    app = TranscriptionApp(controller)
    api = MagicMock()
    with patch.object(app, 'build_interface', return_value=api), patch('uvicorn.run') as run:
        app.launch(server_name='127.0.0.1', server_port=8765)
    run.assert_called_once_with(api, host='127.0.0.1', port=8765, log_level='info')
