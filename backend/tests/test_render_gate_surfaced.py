"""Render-gate ↔ rail-admission consistency (run_c014a4f3).

The Canvas OUTPUTS rail admits SwarmWS-OUTSIDE files (is_canvas_surfaceable), but
GET /workspace/file's absolute-path gate (_resolve_file_path) rejected any path not
under $HOME → "listed in rail but 400 on render". Fix: the render gate allows an
external absolute path IFF it was SURFACED this session (session_registry.surfaced_paths),
via an `allowed_external` set threaded ONLY from the GET-file caller. PUT and the other
callers pass nothing → home-only unchanged (no write-widening — the Gate-0 hole).

These tests lock:
  - external absolute path NOT in the allow-set → still 400 (hole stays closed: /etc/passwd)
  - external absolute path IN the allow-set → resolves (rail row renders)
  - allowed_external=None (PUT + default) → home-only 400 preserved
  - the allow-set membership uses RESOLVED string form (walk-stage un-resolved abs must match)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from routers.workspace_api import _resolve_file_path


def test_external_abs_not_in_allowset_still_400(tmp_path: Path):
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    ext = tmp_path / "outside" / "secret.txt"
    ext.parent.mkdir()
    ext.write_text("x")
    # No allow-set (PUT / default) → home-only guard rejects (unless tmp is under $HOME,
    # which it is not here). Assert 400.
    with pytest.raises(HTTPException) as ei:
        _resolve_file_path(str(ext), ws)
    assert ei.value.status_code == 400


def test_external_abs_in_allowset_resolves(tmp_path: Path):
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    ext = tmp_path / "extrepo" / "hello.py"
    ext.parent.mkdir()
    ext.write_text("print(1)")
    resolved = str(ext.resolve())
    target, is_external = _resolve_file_path(str(ext), ws, allowed_external={resolved})
    assert is_external is True
    assert str(target) == resolved


def test_allowset_does_not_widen_a_path_not_in_it(tmp_path: Path):
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    allowed = tmp_path / "extrepo" / "ok.py"
    allowed.parent.mkdir()
    allowed.write_text("ok")
    other = tmp_path / "extrepo" / "NOT_surfaced.py"
    other.write_text("nope")
    # allow-set has only `allowed`; `other` must still 400 even with a set present.
    with pytest.raises(HTTPException) as ei:
        _resolve_file_path(str(other), ws, allowed_external={str(allowed.resolve())})
    assert ei.value.status_code == 400


def test_none_allowset_preserves_home_only_guard(tmp_path: Path):
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    ext = tmp_path / "x" / "f.py"
    ext.parent.mkdir()
    ext.write_text("x")
    # Explicit None (the PUT/write path) → home-only 400.
    with pytest.raises(HTTPException):
        _resolve_file_path(str(ext), ws, allowed_external=None)


def test_home_under_path_still_allowed_without_allowset(tmp_path, monkeypatch):
    # A path under $HOME resolves with no allow-set (unchanged behavior).
    home = Path.home()
    ws = home / ".swarm-ai" / "SwarmWS"
    # Use an existing home-under dir string; _resolve_file_path does not stat here.
    target, is_external = _resolve_file_path(str(home / "somefile.txt"), ws)
    assert str(target) == str((home / "somefile.txt").resolve())


def test_record_surfaced_path_resolves_and_is_session_scoped(tmp_path):
    from core import session_registry as sr
    sid = "sess-test-abc"
    sr.surfaced_paths.pop(sid, None)
    # A walk-stage-style un-resolved abs (has a '/./' segment) must be stored resolved,
    # matching the render gate's str(target) form.
    raw = f"{tmp_path}/./sub/../f.py"
    sr.record_surfaced_path(sid, raw)
    stored = sr.surfaced_paths[sid]
    assert str(Path(raw).resolve()) in stored
    # Another session does NOT see it (per-session isolation).
    assert "other-sess" not in sr.surfaced_paths or str(Path(raw).resolve()) not in sr.surfaced_paths.get("other-sess", set())
    # falsy inputs are no-ops (never raise)
    sr.record_surfaced_path(None, raw)
    sr.record_surfaced_path(sid, "")
    sr.surfaced_paths.pop(sid, None)


def test_gate0_hole_stays_closed_etc_passwd_never_in_allowset(tmp_path):
    """The Gate-0 hole: /etc/passwd is is_canvas_surfaceable=True, but it is only
    RENDERABLE if it was actually recorded (surfaced) this session. A session that
    never touched it has an empty/None allow-set → still 400."""
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    from core import session_registry as sr
    sid = "sess-hole"
    sr.surfaced_paths.pop(sid, None)  # nothing surfaced
    allow = sr.surfaced_paths.get(sid) or None
    assert allow is None
    with pytest.raises(HTTPException) as ei:
        _resolve_file_path("/etc/passwd", ws, allowed_external=allow)
    assert ei.value.status_code == 400
