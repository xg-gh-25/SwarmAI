"""scripts/smoke_import.py checks backend/ wherever it is started, and never passes
having checked nothing (design 2026-10-27-pipeline-hygiene §3).

Methodology: the real script, loaded as a module; the module walk is pointed at a
directory with no Python package to model "walked the wrong directory".

Invariants:
- BACKEND is this checkout's backend/, independent of the cwd;
- a walk that finds 0 modules exits 1 (it used to print "All 0 ... successfully").
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_import.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_import_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backend_is_found_from_the_script_location():
    mod = _load()
    assert mod.BACKEND == Path(__file__).resolve().parents[1]
    assert (mod.BACKEND / "config.py").is_file()


def test_no_module_found_is_a_failure(tmp_path, monkeypatch, capsys):
    mod = _load()
    (tmp_path / "notes.txt").write_text("not python", encoding="utf-8")
    monkeypatch.setattr(mod, "BACKEND", tmp_path)
    monkeypatch.chdir(tmp_path)
    assert mod.main() == 1
    assert "nothing was checked" in capsys.readouterr().out
