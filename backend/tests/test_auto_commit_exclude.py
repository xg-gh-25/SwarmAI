"""R29 regression: auto-commit must NOT sweep a sibling live session's in-flight edits.

Drives the REAL WorkspaceAutoCommitHook._smart_commit against a REAL temp git repo and
asserts the actual committed tree. Mutation-provable: removing the _unstage_paths
call (exclude) → the sibling file gets committed → these fail.
"""
import subprocess

import pytest

from hooks.auto_commit_hook import WorkspaceAutoCommitHook


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "ws"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.co")
    _git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "seed")
    return r


def _committed_files(repo) -> set[str]:
    out = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


class TestAutoCommitExclude:
    def test_sibling_touched_file_is_not_committed(self, repo):
        # my work + an auto-generated file + a sibling's in-flight edit all dirty
        (repo / "my_change.py").write_text("mine\n")
        (repo / "DailyActivity.md").write_text("generated\n")  # not in any touched set
        sibling = repo / "sibling_wip.py"
        sibling.write_text("sibling in progress\n")

        hook = WorkspaceAutoCommitHook()
        hook._smart_commit(str(repo), exclude={str(sibling)})

        committed = _committed_files(repo)
        assert "my_change.py" in committed, "own work must be committed"
        assert "DailyActivity.md" in committed, "auto-generated files must still be committed"
        assert "sibling_wip.py" not in committed, "sibling in-flight edit must NOT be swept in"
        # and the sibling's change must survive on disk (working tree untouched)
        assert sibling.read_text() == "sibling in progress\n"

    def test_no_exclude_behaves_like_add_all(self, repo):
        # empty exclude → prior behavior: everything committed
        (repo / "a.py").write_text("a\n")
        (repo / "b.py").write_text("b\n")
        hook = WorkspaceAutoCommitHook()
        hook._smart_commit(str(repo), exclude=set())
        committed = _committed_files(repo)
        assert {"a.py", "b.py"} <= committed

    def test_exclude_outside_repo_is_ignored(self, repo, tmp_path):
        # a path outside the repo must not break the commit
        (repo / "c.py").write_text("c\n")
        outside = tmp_path / "elsewhere.py"
        hook = WorkspaceAutoCommitHook()
        hook._smart_commit(str(repo), exclude={str(outside)})
        assert "c.py" in _committed_files(repo)


class TestOverlapExcludeLogic:
    """Gate-2 finding B: a file edited by BOTH the committing session and a
    sibling must NOT be excluded (else the committer's own change is dropped)."""

    def test_shared_file_not_excluded(self, monkeypatch):
        from core import session_registry

        class _Unit:
            def __init__(self, touched):
                self._hook_session_context = {"_files_touched": set(touched)}

        class _Router:
            _units = {
                "me": _Unit(["/ws/shared.py", "/ws/mine_only.py"]),
                "sib": _Unit(["/ws/shared.py", "/ws/sib_only.py"]),
            }

        monkeypatch.setattr(session_registry, "session_router", _Router())
        exclude = WorkspaceAutoCommitHook._other_live_sessions_touched("me")
        # sibling-only file excluded; shared + mine-only NOT excluded
        assert "/ws/sib_only.py" in exclude
        assert "/ws/shared.py" not in exclude, "shared file must stay committable"
        assert "/ws/mine_only.py" not in exclude

    def test_registry_none_is_failsafe(self, monkeypatch):
        from core import session_registry
        monkeypatch.setattr(session_registry, "session_router", None)
        assert WorkspaceAutoCommitHook._other_live_sessions_touched("me") == set()


class TestR1AutoCommitDoor:
    """R1 auto-commit door: _smart_commit must NOT auto-commit un-reviewed CODE
    (bypassing the Bash adversarial-commit gate). Mirrors the gate's coverage
    semantics (P8). Drives REAL _smart_commit against a REAL repo + REAL markers.
    Mutation-provable: dropping the _uncovered_code_paths union → the un-reviewed
    .py gets committed → test_uncovered_code_is_withheld fails.
    """

    import os as _os

    def _write_marker(self, audit_dir, session_id, reviewed_paths, ts=1):
        """Plant a session_<sid>_adv_<ts>.marker. reviewed_paths=None → key ABSENT
        (unbounded); a list (incl. []) → the covered set."""
        import json
        m = audit_dir / f"session_{session_id}_adv_{ts}.marker"
        payload = {"session_id": session_id}
        if reviewed_paths is not None:
            payload["reviewed_paths"] = reviewed_paths
        m.write_text(json.dumps(payload))

    @pytest.fixture
    def audit_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "audit"
        d.mkdir()
        from core import security_hooks
        monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", d)
        return d

    def test_uncovered_code_is_withheld(self, repo, audit_dir):
        """A marker covering ONLY covered.py → uncovered.py (code) is withheld;
        covered.py (code, reviewed) + notes.md (sediment) are committed."""
        import os
        (repo / "covered.py").write_text("reviewed\n")
        (repo / "uncovered.py").write_text("NOT reviewed\n")
        (repo / "notes.md").write_text("sediment\n")  # non-code always commits
        self._write_marker(audit_dir, "sess1",
                            [os.path.realpath(str(repo / "covered.py"))])

        WorkspaceAutoCommitHook()._smart_commit(str(repo), exclude=set(), session_id="sess1")

        committed = _committed_files(repo)
        assert "covered.py" in committed, "reviewed code must commit"
        assert "notes.md" in committed, "sediment must always commit"
        assert "uncovered.py" not in committed, "un-reviewed code must be withheld"
        # withheld code survives on disk for a later gated commit
        assert (repo / "uncovered.py").read_text() == "NOT reviewed\n"

    def test_uncovered_mjs_is_withheld(self, repo, audit_dir):
        """Gate-2 MED regression: .mjs is tracked executable JS — an uncovered
        .mjs must be withheld like .js (was a live miss before the ext set grew)."""
        import os
        (repo / "tool.mjs").write_text("export const x=1\n")
        (repo / "keep.md").write_text("s\n")
        self._write_marker(audit_dir, "sess1", [])  # bounded, covers nothing
        WorkspaceAutoCommitHook()._smart_commit(str(repo), exclude=set(), session_id="sess1")
        committed = _committed_files(repo)
        assert "tool.mjs" not in committed, "un-reviewed .mjs must be withheld"
        assert "keep.md" in committed

    def test_no_marker_session_not_gated(self, repo, audit_dir):
        """No adversarial marker (non-pipeline chat) → this door does NOT gate;
        auto_commit keeps sedimenting AND commits code (the documented narrow gap)."""
        (repo / "hand_edit.py").write_text("chat edit\n")
        (repo / "daily.md").write_text("sediment\n")
        # audit_dir is empty → has_marker False
        WorkspaceAutoCommitHook()._smart_commit(str(repo), exclude=set(), session_id="sess1")
        committed = _committed_files(repo)
        assert {"hand_edit.py", "daily.md"} <= committed

    def test_unbounded_marker_commits_all(self, repo, audit_dir):
        """A path-less marker (reviewed_paths key ABSENT) = unbounded → commit all
        (back-compat parity with the Bash gate's approve)."""
        (repo / "x.py").write_text("x\n")
        self._write_marker(audit_dir, "sess1", None)  # key absent → unbounded
        WorkspaceAutoCommitHook()._smart_commit(str(repo), exclude=set(), session_id="sess1")
        assert "x.py" in _committed_files(repo)

    def test_empty_session_id_not_gated(self, repo, audit_dir):
        """Empty session_id → withhold nothing (fail-open, matches gate)."""
        (repo / "y.py").write_text("y\n")
        self._write_marker(audit_dir, "sess1", [])  # would withhold if session matched
        WorkspaceAutoCommitHook()._smart_commit(str(repo), exclude=set(), session_id="")
        assert "y.py" in _committed_files(repo)

    def test_empty_coverage_withholds_all_code(self, repo, audit_dir):
        """A bounded marker with reviewed_paths=[] (reviewed NOTHING) → every code
        path is uncovered → withheld; sediment still commits."""
        (repo / "a.py").write_text("a\n")
        (repo / "note.md").write_text("s\n")
        self._write_marker(audit_dir, "sess1", [])  # bounded, covers nothing
        WorkspaceAutoCommitHook()._smart_commit(str(repo), exclude=set(), session_id="sess1")
        committed = _committed_files(repo)
        assert "a.py" not in committed, "no code reviewed → all code withheld"
        assert "note.md" in committed, "sediment always commits"
