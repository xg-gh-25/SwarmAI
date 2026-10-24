"""One rule for where the deployed backend lives (design
2026-10-24-swarmai-usable-by-anyone §3.1): runtime/<current>/ when it holds the
executable, else the legacy daemon/ — in Python (utils/runtime_paths) and in the
launchd wrapper (desktop/resources/daemon/swarmai_backend.sh) alike; daemon-lib.sh
deploys into the runtime and writes `current`.
"""
import subprocess
from pathlib import Path

from utils import runtime_paths as rp

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "desktop" / "resources" / "daemon" / "swarmai_backend.sh"


def _home(tmp_path, monkeypatch):
    data = tmp_path / ".swarm-ai"
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    return data


def test_current_runtime_wins_and_legacy_is_the_fallback(tmp_path, monkeypatch):
    data = _home(tmp_path, monkeypatch)
    assert rp.current_backend_dir() == data / "daemon"
    (data / "runtime" / "2.4.0").mkdir(parents=True)
    (data / "runtime" / "current").write_text("2.4.0\n")
    assert rp.current_backend_dir() == data / "daemon"         # no executable yet
    (data / "runtime" / "2.4.0" / "python-backend").write_text("x")
    assert rp.current_backend_dir() == data / "runtime" / "2.4.0"
    (data / "runtime" / "current").write_text("../evil")
    assert rp.current_version() is None and rp.current_backend_dir() == data / "daemon"


def _wrapper_choice(home: Path) -> str:
    text = WRAPPER.read_text()
    start = text.index('RUNTIME_ROOT="${HOME}/.swarm-ai/runtime"')
    end = text.index('DAEMON_BINARY="${DAEMON_HOME}/python-backend"')
    snippet = text[start:end] + 'DAEMON_BINARY="${DAEMON_HOME}/python-backend"\necho "$DAEMON_BINARY"\n'
    return subprocess.run(["bash", "-c", snippet], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, check=True).stdout.strip()


def test_wrapper_runs_the_current_runtime(tmp_path):
    legacy = f"{tmp_path}/.swarm-ai/daemon/python-backend"
    assert _wrapper_choice(tmp_path) == legacy
    rt = tmp_path / ".swarm-ai" / "runtime"
    (rt / "2.4.0").mkdir(parents=True)
    exe = rt / "2.4.0" / "python-backend"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    (rt / "current").write_text("2.4.0")
    assert _wrapper_choice(tmp_path) == str(exe)
    (rt / "current").write_text("../x")
    assert _wrapper_choice(tmp_path) == legacy


def test_dev_deploy_targets_the_runtime_and_writes_current():
    lib = (ROOT / "scripts" / "daemon-lib.sh").read_text()
    assert 'DAEMON_DIR="${RUNTIME_ROOT}/${_APP_VERSION}"' in lib
    assert 'mv "$RUNTIME_ROOT/current.tmp" "$RUNTIME_ROOT/current"' in lib
