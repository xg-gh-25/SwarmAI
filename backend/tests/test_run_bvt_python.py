"""scripts/run_bvt.sh runs the checkout's own venv python when the checkout has one
(backend/tests/conftest.py refuses any other python there), else the python on PATH (CI).

Question each test answers: run for real against stub pythons, does the script pick the
venv's python when backend/.venv exists, and PATH's python when it does not?
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_bvt.sh"


def _stub(path: Path, says: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {says}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _checkout(tmp_path: Path, venv: bool) -> Path:
    root = tmp_path / ("with" if venv else "without")
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "run_bvt.sh")
    tests = root / "backend" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_a.py").write_text("")
    (tests / "bvt_manifest.txt").write_text("test_a.py\n")
    if venv:
        _stub(root / "backend" / ".venv" / "bin" / "python", "VENV_PYTHON")
    return root


def _run(root: Path, tmp_path: Path) -> str:
    bin_dir = tmp_path / "pathbin"
    _stub(bin_dir / "python", "PATH_PYTHON")
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    r = subprocess.run(["bash", str(root / "scripts" / "run_bvt.sh")], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_a_checkout_with_a_venv_runs_its_python(tmp_path):
    assert "VENV_PYTHON" in _run(_checkout(tmp_path, venv=True), tmp_path)


def test_a_checkout_without_a_venv_runs_the_python_on_path(tmp_path):
    assert "PATH_PYTHON" in _run(_checkout(tmp_path, venv=False), tmp_path)
