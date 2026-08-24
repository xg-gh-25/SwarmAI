"""Security tests for the base_path workspace-tree allowlist and validate_path
containment (TT V2265734761 — Cataphract Critical RCE chain, fix 1 + fix 2).

The RCE chain's first link was ``get_workspace_root(agent_id, base_path)``
returning ``Path(base_path)`` verbatim, allowing a caller to write ANY disk
location (``base_path=/`` → ``~/.aws/credentials``, launchd plists, cross-user
files). These tests pin the workspace-tree allowlist that closes it, and the
``validate_path`` containment hardening that closes the ``workspace_root='/'``
startswith bypass — WITHOUT breaking the in-workspace symlink support the
current code deliberately allows.

Methodology: direct unit calls to the pure functions (get_workspace_root /
validate_path) with the cached workspace path monkeypatched to a tmp dir, so no
real filesystem/user data is touched.
"""

import os

import pytest
from fastapi import HTTPException

from routers import workspace as ws


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Point get_cached_workspace_path() at a tmp workspace root."""
    workspace = tmp_path / "SwarmWS"
    workspace.mkdir()
    monkeypatch.setattr(
        "core.initialization_manager.initialization_manager.get_cached_workspace_path",
        lambda: str(workspace),
    )
    return workspace


class TestBasePathAllowlist:
    """AC1: get_workspace_root rejects base_path outside the workspace tree."""

    def test_base_path_root_rejected(self, tmp_workspace):
        """base_path='/' (arbitrary-disk write) → 403."""
        with pytest.raises(HTTPException) as exc:
            ws.get_workspace_root("agent1", "/")
        assert exc.value.status_code == 403

    def test_base_path_home_rejected(self, tmp_workspace):
        """base_path=$HOME → 403 — closes the ~/.aws-under-home hole a home-tree
        allowlist would have missed (workspace is under home, not vice-versa)."""
        with pytest.raises(HTTPException) as exc:
            ws.get_workspace_root("agent1", str(os.path.expanduser("~")))
        assert exc.value.status_code == 403

    def test_base_path_etc_rejected(self, tmp_workspace):
        """base_path=/etc → 403."""
        with pytest.raises(HTTPException) as exc:
            ws.get_workspace_root("agent1", "/etc")
        assert exc.value.status_code == 403

    def test_base_path_dotdot_escape_rejected(self, tmp_workspace):
        """A base_path that resolves outside the workspace via .. → 403."""
        with pytest.raises(HTTPException) as exc:
            ws.get_workspace_root("agent1", str(tmp_workspace / ".." / ".." / "etc"))
        assert exc.value.status_code == 403

    def test_base_path_equal_workspace_allowed(self, tmp_workspace):
        """base_path == the workspace root (the only live case) is allowed."""
        result = ws.get_workspace_root("agent1", str(tmp_workspace))
        assert result.resolve() == tmp_workspace.resolve()

    def test_base_path_descendant_allowed(self, tmp_workspace):
        """base_path under the workspace root is allowed."""
        sub = tmp_workspace / "Projects"
        sub.mkdir()
        result = ws.get_workspace_root("agent1", str(sub))
        assert result.resolve() == sub.resolve()

    def test_no_base_path_uses_default(self, tmp_workspace):
        """No base_path → the default cached workspace path, no allowlist check."""
        result = ws.get_workspace_root("agent1", None)
        assert result.resolve() == tmp_workspace.resolve()


class TestValidatePathContainment:
    """AC2: validate_path containment. HONEST scope (verified empirically): the
    workspace_root='/' bypass is closed by fix1 (base_path=/ rejected upstream so
    workspace_root can never BE '/'), NOT by validate_path in isolation —
    is_relative_to('/') is still True for '/etc/passwd'. What fix2 changes here is
    removing the resolve()-based startswith OR-branch (a redundant path that
    compared the RESOLVED full_path), replacing the containment test with a single
    lexical is_relative_to on the UN-resolved path. That preserves the intended
    behavior (in-workspace relative + symlink names pass; traversal blocked) with a
    simpler, non-resolve-dependent check. These tests pin THAT contract."""

    def test_traversal_still_blocked(self):
        """../.. escape stays blocked (regression guard)."""
        with pytest.raises(HTTPException) as exc:
            ws.validate_path(_P("/tmp/ws_root"), "../../etc/passwd")
        assert exc.value.status_code == 403

    def test_absolute_requested_path_blocked(self):
        """An absolute requested path stays blocked."""
        with pytest.raises(HTTPException) as exc:
            ws.validate_path(_P("/tmp/ws_root"), "/etc/passwd")
        assert exc.value.status_code == 403

    def test_in_workspace_relative_allowed(self):
        """A normal in-workspace relative path is allowed (no false-block)."""
        result = ws.validate_path(_P("/tmp/ws_root"), "Knowledge/Notes/x.md")
        assert str(result) == os.path.join("/tmp/ws_root", "Knowledge/Notes/x.md")

    def test_in_workspace_symlink_name_allowed(self):
        """A path naming an in-workspace entry (symlink or not) is allowed —
        is_relative_to on the un-resolved path preserves symlink support."""
        result = ws.validate_path(_P("/tmp/ws_root"), "mylink")
        assert str(result) == os.path.join("/tmp/ws_root", "mylink")


# -- helpers ---------------------------------------------------------------
from pathlib import Path as _P  # noqa: E402
