from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_single_systemd_unit_matches_local_checkout_contract():
    systemd_dir = ROOT / "deploy" / "systemd"
    assert sorted(path.name for path in systemd_dir.glob("*.service")) == [
        "echoscript.service"
    ]
    unit = (systemd_dir / "echoscript.service").read_text(encoding="utf-8")
    assert "User=nvidia" in unit
    assert "Group=nvidia" in unit
    assert "WorkingDirectory=/home/nvidia/ping/echoscript" in unit
    assert "EnvironmentFile=-/home/nvidia/ping/echoscript/.env" in unit
    assert (
        "ExecStart=/home/nvidia/miniforge3/bin/uv run --no-sync python "
        "/home/nvidia/ping/echoscript/serve.py"
    ) in unit
    assert "Wants=network-online.target" in unit
    assert "After=network-online.target" in unit
    assert "KillMode=control-group" in unit
    assert "TimeoutStopSec=30" in unit


def test_single_environment_example_is_local_and_has_no_web_auth():
    systemd_dir = ROOT / "deploy" / "systemd"
    assert sorted(path.name for path in systemd_dir.glob("*.env.example")) == [
        "echoscript.env.example"
    ]
    env = (systemd_dir / "echoscript.env.example").read_text(encoding="utf-8")
    assert "ECHOSCRIPT_DATA_DIR=/home/nvidia/ping/echoscript/data" in env
    assert "ECHOSCRIPT_WEB_HOST=0.0.0.0" in env
    assert "ECHOSCRIPT_WEB_USERNAME" not in env
    assert "ECHOSCRIPT_WEB_PASSWORD" not in env
    assert "ECHOSCRIPT_IDLE_UNLOAD_SECONDS" not in env
    assert "HF_TOKEN=" in env


def test_serve_entrypoint_is_minimal():
    source = (ROOT / "serve.py").read_text(encoding="utf-8")
    assert "from echoscript.web import run_web" in source
    assert 'if __name__ == "__main__":' in source
    assert "run_web()" in source
