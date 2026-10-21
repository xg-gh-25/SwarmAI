"""``run-update --files-touched`` refuses an entry that is several paths joined by spaces.

Methodology: a run is created in a temp workspace that also holds a few real files,
and ``artifact_cli.py run-update`` runs as a subprocess from that directory (as
agents call it). The decision is made by the file system: an entry is refused only
when the whole string is not a path and every space-separated part is one. Property:
a quoted, space-joined list exits 1 and leaves files_touched unchanged; separate
arguments, a JSON array, an existing path with a space in it and an entry whose
parts do not exist are stored as before.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
RUN_ID = "run_joined01"


@pytest.fixture
def ws(tmp_path):
    run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "id": RUN_ID, "project": "P", "requirement": "r", "profile": "direct",
        "status": "running", "stages": [], "files_touched": [],
        "created_at": "2026-10-21T00:00:00+00:00", "updated_at": "2026-10-21T00:00:00+00:00",
    }), encoding="utf-8")
    for f in ("src/a.py", "src/b.py", "a.b c/d.py"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x", encoding="utf-8")
    return tmp_path


def _update(ws: Path, *files: str) -> subprocess.CompletedProcess:
    env = {"SWARM_WORKSPACE": str(ws), "SWARM_TODO_DB": str(ws / ".todos.db"),
           "PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND)}
    return subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "artifact_cli.py"), "run-update",
         "--project", "P", "--run-id", RUN_ID, "--files-touched", *files],
        capture_output=True, text=True, env=env, cwd=str(ws), timeout=60)


def _files(ws: Path) -> list:
    p = ws / "Projects" / "P" / ".artifacts" / "runs" / RUN_ID / "run.json"
    return json.loads(p.read_text(encoding="utf-8"))["files_touched"]


def test_joined_relative_paths_refused(ws):
    r = _update(ws, "src/a.py src/b.py")
    assert r.returncode == 1
    assert "several paths joined by spaces" in r.stderr
    assert _files(ws) == []


def test_joined_absolute_paths_refused(ws):
    assert _update(ws, f"{ws}/src/a.py {ws}/src/b.py").returncode == 1


def test_separate_arguments_stored(ws):
    r = _update(ws, "src/a.py", "src/b.py")
    assert r.returncode == 0, r.stderr
    assert _files(ws) == ["src/a.py", "src/b.py"]


def test_json_array_stored(ws):
    assert _update(ws, '["src/a.py", "src/b.py"]').returncode == 0
    assert _files(ws) == ["src/a.py", "src/b.py"]


def test_existing_path_with_a_space_is_one_path(ws):
    assert _update(ws, "a.b c/d.py").returncode == 0
    assert _files(ws) == ["a.b c/d.py"]


def test_parts_that_do_not_exist_are_not_judged(ws):
    assert _update(ws, "new/x.py new/y.py").returncode == 0
