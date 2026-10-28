"""A packaged daemon's eval reports carry the digest the release gate computes.

Questions each test answers:
- does a frozen daemon with the build-time manifest compute the same code_digest as
  the gate does in the checkout the manifest was written from?
- does a frozen daemon without a usable manifest say so (and stay stale, never pass)?
- does the build script write the manifest from the source checkout and package it?
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from scripts import eval_runner as er

REPO = Path(__file__).resolve().parents[2]


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "backend" / "scripts").mkdir(parents=True)
    for rel in er._GATE_CODE_PATHS:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {rel}\n")
    (root / "backend" / "scripts" / "probe_x.py").write_text("print('X_OK')\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    return root


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    import utils.backend_bin as bb
    manifest = tmp_path / "bundle" / "generated" / "eval_gate_inputs.json"
    monkeypatch.setattr(bb, "is_frozen", lambda: True)
    monkeypatch.setattr(er, "_gate_manifest_path", lambda: manifest)
    # the packaged daemon's own checkout guess has no source files
    monkeypatch.setattr(er, "_THIS_CHECKOUT", tmp_path / "no-source-here")
    return manifest


def test_a_packaged_daemon_matches_the_checkout_it_was_built_from(tmp_path, frozen):
    repo = _repo(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    er.write_gate_manifest(repo, frozen)
    assert er.compute_code_digest(ws) == er.compute_code_digest(ws, code_root=repo)


def test_a_probe_edited_after_the_build_makes_the_daemon_report_stale(tmp_path, frozen):
    repo = _repo(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    er.write_gate_manifest(repo, frozen)
    (repo / "backend" / "scripts" / "probe_x.py").write_text("print('X_OK')  # weakened\n")
    assert er.compute_code_digest(ws) != er.compute_code_digest(ws, code_root=repo)


@pytest.mark.parametrize("body", [None, "{not json", json.dumps({"inputs": []}),
                                  json.dumps({"inputs": [1, 2]})])
def test_no_usable_manifest_is_logged_and_never_matches(tmp_path, frozen, caplog, body):
    repo = _repo(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    if body is not None:
        frozen.parent.mkdir(parents=True)
        frozen.write_text(body)
    with caplog.at_level(logging.ERROR):
        packaged = er.compute_code_digest(ws)
    assert "no usable gate manifest" in caplog.text
    assert packaged != er.compute_code_digest(ws, code_root=repo)


def test_a_checkout_ignores_any_manifest(tmp_path, monkeypatch):
    import utils.backend_bin as bb
    repo = _repo(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"inputs": ["x:1"]}))
    monkeypatch.setattr(bb, "is_frozen", lambda: False)
    monkeypatch.setattr(er, "_gate_manifest_path", lambda: manifest)
    monkeypatch.setattr(er, "_THIS_CHECKOUT", repo)
    assert er.compute_code_digest(ws) == er.compute_code_digest(ws, code_root=repo)


def test_the_build_writes_the_manifest_from_the_source_checkout_and_packages_it():
    script = (REPO / "desktop" / "scripts" / "build-backend.sh").read_text()
    write = script.index("write_gate_manifest(Path(sys.argv[1]), Path('generated/eval_gate_inputs.json'))\" \"$PROJECT_ROOT/..\"")
    assert write < script.index("pyinstaller backend.spec")
    assert "datas += [('generated/eval_gate_inputs.json', 'generated')]" in script
