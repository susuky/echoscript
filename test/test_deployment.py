from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tracked_systemd_example_and_readme_agree():
    unit = (ROOT/'deploy/systemd/echoscript.service.example').read_text()
    assert 'WorkingDirectory=/path/to/echoscript' in unit
    assert 'EnvironmentFile=-/path/to/echoscript/.env' in unit
    assert 'KillMode=control-group' in unit
    assert 'TimeoutStopSec=30' in unit
    readme = (ROOT/'README.md').read_text()
    assert 'cp .env.example .env' in readme
    assert 'echoscript.service.example' in readme


def test_environment_example_contains_real_settings():
    env = (ROOT/'.env.example').read_text()
    assert 'ECHOSCRIPT_MAX_UPLOAD_BYTES=' in env
    assert 'ECHOSCRIPT_JOB_RETENTION_DAYS=' in env
    assert 'HF_TOKEN=' in env
    assert 'ECHOSCRIPT_MODEL_IDLE_TIMEOUT_SECONDS=300' in env


def test_public_setup_files_use_portable_paths_and_english_readme():
    for name in ('README.md', '.env.example', 'deploy/systemd/echoscript.service.example',
                 'deploy/systemd/echoscript-user.service.example'):
        text = (ROOT/name).read_text()
        assert '/home/nvidia' not in text and '/Users/' not in text
        assert 'User=nvidia' not in text and 'Group=nvidia' not in text
    assert '## HTTP API' in (ROOT/'README.md').read_text()
    user_unit = (ROOT/'deploy/systemd/echoscript-user.service.example').read_text()
    assert 'WantedBy=default.target' in user_unit
    assert 'ExecStart=/path/to/echoscript/.venv/bin/python' in user_unit


def test_packaging_includes_frontend_and_explicit_api_dependencies():
    config = (ROOT/'pyproject.toml').read_text()
    assert '"fastapi>=' in config
    assert '"uvicorn>=' in config
    assert '"gradio>=' not in config
    assert '"web_assets/*"' in config


def test_serve_entrypoint_is_minimal():
    source = (ROOT/'serve.py').read_text()
    assert 'from echoscript.web import run_web' in source
    assert 'run_web()' in source
