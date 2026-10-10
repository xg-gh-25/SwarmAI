"""Tests for ~ (tilde) expansion in workspace path resolution.

Regression coverage for the FileViewer 404 bug: a path beginning with ``~``
(e.g. ``~/Desktop/x.md``) was NOT run through ``os.path.expanduser``, so
``os.path.isabs("~/...")`` returned False, the path fell into the
workspace-relative branch, and resolved to a bogus
``<workspace>/~/Desktop/...`` that is not a file → GET /workspace/file 404.

Both resolvers must expand a LEADING ``~`` (P8 all-doors):
- ``_resolve_file_path``        (GET/PUT/meta/committed/resolve callers)
- ``resolve_path_to_physical``  (streaming-orchestrator written-file abs-path)

Security invariant: expanding ``~`` → ``$HOME`` must NOT widen the home-only
guard, and must NOT relax the PUT write-path boundary (allowed_external stays
GET-only). Those invariants are asserted here too.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.workspace_api import _resolve_file_path, resolve_path_to_physical


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A tmp $HOME with a real file at ~/sub/card.md, and a separate workspace."""
    home = tmp_path / "home"
    (home / "sub").mkdir(parents=True)
    target = home / "sub" / "card.md"
    target.write_text("# card", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Path.home() reads $HOME on POSIX; patch both for safety.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return {"home": home, "target": target, "workspace": workspace}


# --- AC1 / AC5: a leading ~ resolves to the expanded $HOME absolute path ---

def test_resolve_file_path_expands_leading_tilde(fake_home):
    """AC1: _resolve_file_path('~/sub/card.md') → the real $HOME file, is_file True."""
    target, is_external = _resolve_file_path("~/sub/card.md", fake_home["workspace"])
    assert target == fake_home["target"].resolve()
    assert target.is_file()
    # It lives outside the workspace → is_external True (correct, read-only render).
    assert is_external is True


def test_resolve_path_to_physical_expands_leading_tilde(fake_home):
    """AC5: resolve_path_to_physical('~/sub/card.md') → resolved dict (all-doors)."""
    result = resolve_path_to_physical("~/sub/card.md", fake_home["workspace"])
    assert result is not None
    assert result["absolute"] == str(fake_home["target"].resolve())


# --- AC4: non-~ paths are byte-identical to before (no regression) ---

def test_absolute_path_under_home_unchanged(fake_home):
    """An already-absolute $HOME path still resolves (unchanged branch)."""
    abs_path = str(fake_home["target"])
    target, _ = _resolve_file_path(abs_path, fake_home["workspace"])
    assert target == fake_home["target"].resolve()


def test_workspace_relative_path_unchanged(fake_home):
    """A normal workspace-relative path still resolves under the workspace."""
    (fake_home["workspace"] / "Knowledge").mkdir()
    note = fake_home["workspace"] / "Knowledge" / "n.md"
    note.write_text("x", encoding="utf-8")
    target, is_external = _resolve_file_path("Knowledge/n.md", fake_home["workspace"])
    assert target == note.resolve()
    assert is_external is False


def test_embedded_tilde_not_expanded(fake_home):
    """A non-leading ~ (foo/~/bar) must NOT be expanded — expanduser is leading-only.

    It stays a workspace-relative path (no bogus $HOME injection mid-path).
    """
    # os.path.expanduser leaves an embedded ~ literal; the path then resolves
    # under the workspace and simply does not exist → relative branch, no crash.
    target, is_external = _resolve_file_path("foo/~/bar.md", fake_home["workspace"])
    assert is_external is False
    assert "home" not in str(target).split("/")  # did NOT inject $HOME mid-path


def test_traversal_still_blocked(fake_home):
    """AC4: a .. traversal relative path is still rejected (guard intact)."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _resolve_file_path("../etc/passwd", fake_home["workspace"])
    assert exc.value.status_code == 400


# --- AC2 / AC3: security boundary not widened ---

def test_external_absolute_denied_without_allowlist(fake_home, tmp_path):
    """AC2/AC3: an absolute path OUTSIDE $HOME, with no allowed_external
    (the PUT/write caller shape), is still denied — ~ fix does not relax this."""
    from fastapi import HTTPException
    outside = tmp_path / "outside_home"
    outside.mkdir()
    victim = outside / "secret.txt"
    victim.write_text("s", encoding="utf-8")
    # No allowed_external → home-only guard must fire (victim is not under $HOME).
    with pytest.raises(HTTPException) as exc:
        _resolve_file_path(str(victim), fake_home["workspace"], allowed_external=None)
    assert exc.value.status_code == 400


def test_tilde_cannot_escape_home_guard(fake_home):
    """AC2: a ~-expanded path is under $HOME, so it passes; but the guard that
    makes it pass is the SAME home-only check — ~ is not a bypass, it IS $HOME."""
    # ~/sub/card.md expands under $HOME → allowed (no exception), which is correct.
    target, _ = _resolve_file_path("~/sub/card.md", fake_home["workspace"], allowed_external=None)
    assert str(target).startswith(str(fake_home["home"]))


def test_tilde_otheruser_expands_outside_home_is_denied(fake_home, monkeypatch):
    """AC2 (direct): a ``~otheruser`` form that expands OUTSIDE the current $HOME is
    still 400'd by the home-only guard — ~ is not a bypass even for the ~user form.

    The fake_home fixture sets $HOME to a tmp dir, so a real ~user (e.g. ~root →
    /var/root) is NOT under it. We stub expanduser to make the mapping deterministic
    and OS-independent, then assert the guard fires.
    """
    from fastapi import HTTPException

    outside = fake_home["home"].parent / "elsewhere"
    outside.mkdir()
    # ~otheruser → an absolute path OUTSIDE the current (tmp) $HOME.
    monkeypatch.setattr(
        os.path, "expanduser",
        lambda p: str(outside / "x.md") if p.startswith("~otheruser") else p,
    )
    with pytest.raises(HTTPException) as exc:
        _resolve_file_path("~otheruser/x.md", fake_home["workspace"], allowed_external=None)
    assert exc.value.status_code == 400
