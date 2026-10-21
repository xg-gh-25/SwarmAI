"""``run-update --files-touched`` refuses an entry that is several paths joined by spaces.

Methodology: a run is created in a temp workspace and ``artifact_cli.py run-update``
runs as a subprocess (as agents call it). Property: a quoted, space-separated list
(`--files-touched "$FILES"`) exits 1 with a message naming the fix and leaves
files_touched unchanged; separate arguments, a JSON array and a single path with a
space in a directory name are stored as before.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
RUN_ID = "run_joined01"


def _cli(ws: Path, *args: str) -> subprocess.CompletedProcess:
    env = {"SWARM_WORKSPACE": str(ws), "SWARM_TODO_DB": str(ws / ".todos.db"),
           "PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND)}
    return subprocess.run([sys.executable, str(BACKEND / "scripts" / "artifact_cli.py"), *args],
                          capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=60)


@pytest.fixture
def ws(tmp_path):
    run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "id": RUN_ID, "project": "P", "requirement": "r", "profile": "direct",
        "status": "running", "stages": [], "files_touched": [],
        "created_at": "2026-10-21T00:00:00+00:00", "updated_at": "2026-10-21T00:00:00+00:00",
    }), encoding="utf-8")
    return tmp_path


def _files(ws: Path) -> list:
    p = ws / "Projects" / "P" / ".artifacts" / "runs" / RUN_ID / "run.json"
    return json.loads(p.read_text(encoding="utf-8"))["files_touched"]


def _update(ws: Path, *files: str) -> subprocess.CompletedProcess:
    return _cli(ws, "run-update", "--project", "P", "--run-id", RUN_ID, "--files-touched", *files)


def test_joined_paths_refused(ws):
    r = _update(ws, "desktop/src/a.ts /Users/me/repo/desktop/src/b.ts")
    assert r.returncode == 1
    assert "several paths joined by spaces" in r.stderr
    assert _files(ws) == []


def test_joined_relative_paths_refused(ws):
    assert _update(ws, "backend/a.py backend/tests/test_a.py").returncode == 1


def test_separate_arguments_stored(ws):
    r = _update(ws, "backend/a.py", "backend/tests/test_a.py")
    assert r.returncode == 0, r.stderr
    assert _files(ws) == ["backend/a.py", "backend/tests/test_a.py"]


def test_json_array_stored(ws):
    assert _update(ws, '["backend/a.py", "backend/b.py"]').returncode == 0
    assert _files(ws) == ["backend/a.py", "backend/b.py"]


def test_space_in_a_directory_name_is_one_path(ws):
    p = "/Applications/Visual Studio Code.app/Contents/x.js"
    assert _update(ws, p).returncode == 0
    assert _files(ws) == [p]
