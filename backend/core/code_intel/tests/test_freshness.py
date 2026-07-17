"""Tests for freshness.py — git SHA tracking and staleness detection."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.freshness import (
    FreshnessResult,
    GitError,
    _git,
    check_freshness,
)


@pytest.fixture
def mock_graph():
    """Mock GraphStore with meta values."""
    graph = MagicMock()
    graph.get_meta.side_effect = lambda key: {
        "repo_root": "/tmp/test_repo",
        "last_indexed_commit": "abc123def456",
        "last_full_index": "1700000000.0",
    }.get(key)
    return graph


class TestCheckFreshness:
    """Test the main check_freshness function."""

    def test_no_repo_root(self):
        graph = MagicMock()
        graph.get_meta.return_value = None
        result = check_freshness(graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True

    def test_missing_directory(self, mock_graph):
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": "/nonexistent/path",
            "last_indexed_commit": "abc123",
        }.get(key)
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert "not found" in result.reason

    @patch("core.code_intel.freshness._git")
    def test_never_indexed(self, mock_git_fn, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": None,
        }.get(key)
        mock_git_fn.return_value = "head_sha\n"
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert "Never indexed" in result.reason
        # REGRESSION GUARD (run_9a23dd4a): current_head MUST be populated on the
        # never-indexed path so the 3 marker writers (code_intel_reindex.py:73/129,
        # context_health_hook.py:649 — all guarded by `if freshness.current_head:`)
        # can persist last_indexed_commit. Before the fix this was None → marker
        # never persisted → perpetual full rebuild → 120s timeout flap.
        # Revert the freshness fix and THIS assertion goes RED (mutation-proven).
        assert result.current_head == "head_sha"

    @patch("core.code_intel.freshness._git")
    def test_never_indexed_git_failure_keeps_none(self, mock_git_fn, mock_graph, tmp_path):
        """On the never-indexed path, a genuine git rev-parse failure must leave
        current_head=None (the write-guard stays closed) and NOT crash — the
        marker simply isn't persisted this cycle (correct: we can't know HEAD)."""
        from core.code_intel.freshness import GitError
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": None,
        }.get(key)
        mock_git_fn.side_effect = GitError("rev-parse failed")
        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert result.current_head is None  # guard preserved, no crash

    @patch("core.code_intel.freshness._git")
    def test_up_to_date(self, mock_git_fn, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "abc123",
        }.get(key)
        mock_git_fn.return_value = "abc123\n"
        result = check_freshness(mock_graph)
        assert result.stale is False
        # Fresh path now carries current_head (Gate-2 MED, run_9a23dd4a): the
        # field is set whenever git succeeded, so a --full rebuild on an
        # already-fresh repo can still refresh the marker.
        assert result.current_head == "abc123"

    @patch("core.code_intel.freshness.subprocess.run")
    @patch("core.code_intel.freshness._git")
    def test_normal_incremental(self, mock_git_fn, mock_subprocess, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "old_sha",
        }.get(key)

        # _git calls: rev-parse, diff, rev-list
        mock_git_fn.side_effect = [
            "new_sha\n",           # rev-parse HEAD
            "file1.py\nfile2.py\n",  # diff --name-only
            "3\n",                  # rev-list --count
        ]
        # merge-base check succeeds (ancestor)
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.changed_files == ["file1.py", "file2.py"]
        assert result.commits_behind == 3
        assert result.suggest_full_rebuild is False

    @patch("core.code_intel.freshness.subprocess.run")
    @patch("core.code_intel.freshness._git")
    def test_rebase_detected(self, mock_git_fn, mock_subprocess, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "old_sha",
        }.get(key)
        mock_git_fn.return_value = "new_sha\n"
        # merge-base fails (not ancestor)
        mock_subprocess.return_value = MagicMock(returncode=1)

        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert "rebased away" in result.reason

    @patch("core.code_intel.freshness.subprocess.run")
    @patch("core.code_intel.freshness._git")
    def test_large_change_suggests_rebuild(self, mock_git_fn, mock_subprocess, mock_graph, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_graph.get_meta.side_effect = lambda key: {
            "repo_root": str(tmp_path),
            "last_indexed_commit": "old_sha",
        }.get(key)

        files = [f"file{i}.py" for i in range(120)]
        mock_git_fn.side_effect = [
            "new_sha\n",
            "\n".join(files) + "\n",
            "55\n",
        ]
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = check_freshness(mock_graph)
        assert result.stale is True
        assert result.suggest_full_rebuild is True
        assert len(result.changed_files) == 120


class TestGitCommand:
    """Test the _git helper."""

    @patch("core.code_intel.freshness.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="output\n", stderr=""
        )
        result = _git(Path("/tmp"), ["status"])
        assert result == "output\n"

    @patch("core.code_intel.freshness.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error msg"
        )
        with pytest.raises(GitError, match="error msg"):
            _git(Path("/tmp"), ["bad-command"])

    @patch("core.code_intel.freshness.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=5)
        with pytest.raises(GitError, match="timed out"):
            _git(Path("/tmp"), ["slow-command"])

    @patch("core.code_intel.freshness.subprocess.run")
    def test_git_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(GitError, match="not found"):
            _git(Path("/tmp"), ["status"])


class TestFreshnessResult:
    """Test dataclass defaults."""

    def test_defaults(self):
        result = FreshnessResult(stale=False)
        assert result.changed_files == []
        assert result.commits_behind == 0
        assert result.suggest_full_rebuild is False
        assert result.reason == ""


class TestPersistenceLoopBreaks:
    """Integration (run_9a23dd4a): prove the perpetual-full-rebuild loop breaks.

    Uses a REAL git repo + REAL GraphStore (no mocks) — the round-trip that the
    reindex handler relies on: on a never-indexed graph, check_freshness now
    returns a populated current_head; the caller persists it as
    last_indexed_commit; the NEXT check_freshness sees HEAD==last_commit and
    returns stale=False (no rebuild). Before the fix, current_head was None on
    the never-indexed path, so the marker never persisted and every run was
    'Never indexed' → full rebuild → 120s timeout flap.
    """

    def _init_git_repo(self, tmp_path):
        import subprocess as sp
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }
        sp.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=env)
        (tmp_path / "f.py").write_text("x = 1\n")
        sp.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True, env=env)
        head = sp.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                      capture_output=True, text=True, check=True, env=env).stdout.strip()
        return head

    def test_marker_persists_and_second_check_is_fresh(self, tmp_path):
        from core.code_intel.graph_store import GraphStore

        head = self._init_git_repo(tmp_path)
        db = GraphStore(tmp_path / "code_intel.db")
        db.set_meta("repo_root", str(tmp_path))
        # never indexed: no last_indexed_commit yet
        assert db.get_meta("last_indexed_commit") is None

        # First check: never-indexed → stale + full rebuild, BUT current_head now populated
        fr1 = check_freshness(db)
        assert fr1.stale is True
        assert fr1.suggest_full_rebuild is True
        assert fr1.current_head == head  # THE FIX: was None before

        # Caller persists the marker (mirrors code_intel_reindex.py:73 / :129,
        # context_health_hook.py:649 — the `if freshness.current_head:` writers)
        if fr1.current_head:
            db.set_meta("last_indexed_commit", fr1.current_head)
        assert db.get_meta("last_indexed_commit") == head

        # Second check with no new commits: loop is broken → NOT stale, no rebuild
        fr2 = check_freshness(db)
        assert fr2.stale is False
        assert fr2.suggest_full_rebuild is False
        db.close()


class TestFullRebuildDelegation:
    """Gate-2 HIGH (run_9a23dd4a): the INCREMENTAL job (full=False, 120s) must
    NOT run a full rebuild inline — a full reparse hugs/exceeds 120s and gets
    killed before persisting the marker. When suggest_full_rebuild is True and
    full=False, delegate to the code_intel_full_reindex event (300s job).
    The --full job itself (full=True) still runs inline.
    """

    def _make_project(self, tmp_path, monkeypatch):
        """A Projects/ dir with one never-indexed code_intel.db on a git repo.

        Redirects BOTH path sources the handler uses: Path.home() (for the
        Projects/ iteration) AND load_project_graph (for the DB load, which
        otherwise resolves via the frozen jobs.paths.PROJECTS_DIR).
        """
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        proj = tmp_path / ".swarm-ai" / "SwarmWS" / "Projects" / "P1"
        proj.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sp.run(["git", "init", "-q"], cwd=proj, check=True, env=env)
        (proj / "f.py").write_text("x = 1\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "i"], cwd=proj, check=True, env=env)
        db = GraphStore(proj / "code_intel.db")
        db.set_meta("repo_root", str(proj))  # never indexed: no last_indexed_commit
        db.close()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        # load_project_graph is imported function-locally from core.code_intel,
        # and resolves DB via the frozen PROJECTS_DIR — patch it at the source
        # so the handler operates on OUR never-indexed tmp db.
        import core.code_intel as ci
        monkeypatch.setattr(
            ci, "load_project_graph",
            lambda name: GraphStore(proj / "code_intel.db"),
        )
        import jobs.handlers.code_intel_reindex as handler
        return handler

    def test_incremental_job_delegates_full_rebuild(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        handler = self._make_project(tmp_path, monkeypatch)
        # Run AB: the full-rebuild path now parses via parse_repo_with_coverage
        # (coverage-aware). Patch that entry point so "delegated → nothing parsed
        # inline" stays an exact assertion.
        with patch("jobs.scheduler.emit_event_atomic") as mock_emit, \
             patch("core.code_intel.parser.parse_repo_with_coverage") as mock_parse:
            result = handler.reindex_projects(full=False)
            # Delegated → event emitted, no inline parse
            mock_emit.assert_called_once()
            assert mock_emit.call_args[0][0] == "code_intel_full_reindex"
            mock_parse.assert_not_called()
        statuses = {r["project"]: r["status"] for r in result["projects"]}
        assert statuses.get("P1") == "delegated_full_reindex"

    def test_full_job_runs_inline_not_delegated(self, tmp_path, monkeypatch):
        from unittest.mock import patch
        from core.code_intel.parser import ParseRepoResult
        handler = self._make_project(tmp_path, monkeypatch)
        # full=True → inline parse via the coverage-aware entry point (Run AB).
        with patch("jobs.scheduler.emit_event_atomic") as mock_emit, \
             patch("core.code_intel.parser.parse_repo_with_coverage",
                   return_value=ParseRepoResult(results=[], coverage_holes=[], status="complete")) as mock_parse:
            handler.reindex_projects(full=True)
            # full=True → inline (parse called), NOT delegated
            mock_emit.assert_not_called()
            mock_parse.assert_called_once()


# ─── Run 4b (run_2bad039d, §8.6): spec-details staleness detector ───

class TestSpecDetailsStaleness:
    """detect_spec_details_staleness: CONTENT-HASH based (NOT mtime).

    A spec is stale iff its embedded ``<!-- spec-hash: X -->`` marker is missing or
    != the ``spec_hash`` stamped on its domain in code-intel.json. mtime is
    IRRELEVANT (Gate-1 RESHAPE): a reindex rewrites code-intel.json — mtime bumps —
    while PRESERVING identical domains[], so an mtime detector false-fired all specs.
    """
    import json as _json

    def _write(self, proj, domain_id, spec_hash_in_json, marker_hash_in_spec):
        """Write a code-intel.json with one domain carrying spec_hash, plus a
        spec.md carrying (or lacking) a spec-hash marker. Returns proj dir."""
        import json
        proj.mkdir(exist_ok=True)
        sd = proj / "spec-details"; sd.mkdir(exist_ok=True)
        name = domain_id.split(":", 1)[-1]
        dom = {"id": domain_id, "name": name}
        if spec_hash_in_json is not None:
            dom["spec_hash"] = spec_hash_in_json
        (proj / "code-intel.json").write_text(
            json.dumps({"domains": [dom]}), encoding="utf-8")
        marker = (f"<!-- spec-hash: {marker_hash_in_spec} -->\n"
                  if marker_hash_in_spec is not None else "")
        (sd / f"{name}.spec.md").write_text(f"# 规格:{name}\n{marker}body\n",
                                            encoding="utf-8")
        return proj

    def test_matching_hash_is_fresh(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        h = "a" * 64
        proj = self._write(tmp_path / "P", "domain:orders", h, h)
        assert detect_spec_details_staleness(proj) == []

    def test_mismatched_hash_is_stale(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = self._write(tmp_path / "P", "domain:orders", "a" * 64, "b" * 64)
        assert detect_spec_details_staleness(proj) == ["orders.spec.md"]

    def test_missing_marker_is_stale(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = self._write(tmp_path / "P", "domain:orders", "a" * 64, None)
        assert detect_spec_details_staleness(proj) == ["orders.spec.md"]

    def test_mtime_bump_with_identical_content_is_FRESH(self, tmp_path):
        # THE false-positive the whole reshape exists to kill: rewrite code-intel.json
        # (mtime bumps) but domains[]+spec_hash unchanged → MUST report fresh.
        import os, time
        from core.code_intel.freshness import detect_spec_details_staleness
        h = "c" * 64
        proj = self._write(tmp_path / "P", "domain:orders", h, h)
        assert detect_spec_details_staleness(proj) == []
        # bump code-intel.json mtime WAY past the spec, content identical
        ci = proj / "code-intel.json"
        os.utime(ci, (time.time() + 10_000, time.time() + 10_000))
        assert detect_spec_details_staleness(proj) == []  # mtime-independent

    def test_domain_without_spec_hash_not_flagged(self, tmp_path):
        # A domain with no spec_hash stamp (e.g. pre-reshape doc) → can't judge → not stale.
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = self._write(tmp_path / "P", "domain:orders", None, "a" * 64)
        assert detect_spec_details_staleness(proj) == []

    def test_no_code_intel_returns_empty(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = tmp_path / "P"; (proj / "spec-details").mkdir(parents=True)
        (proj / "spec-details" / "x.spec.md").write_text("x", encoding="utf-8")
        assert detect_spec_details_staleness(proj) == []  # no code-intel.json

    def test_no_spec_dir_returns_empty(self, tmp_path):
        from core.code_intel.freshness import detect_spec_details_staleness
        proj = tmp_path / "P"; proj.mkdir()
        (proj / "code-intel.json").write_text("{}", encoding="utf-8")
        assert detect_spec_details_staleness(proj) == []


# ─── run_4602932d: graded incremental re-analysis (E2E through reindex handler) ───

class TestGradedIncrementalE2E:
    """Drive reindex_projects(full=False) over a REAL mixed changeset and assert
    the grading behavior: COSMETIC files are skipped WITH their nodes conserved
    (the merge-never-drops bond), STRUCTURAL files are re-stored, and the
    aggregate verdict is recorded to meta with a real consumer (SKIP short-circuit).
    """

    def _init_repo(self, tmp_path, monkeypatch):
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        proj = tmp_path / ".swarm-ai" / "SwarmWS" / "Projects" / "P1"
        proj.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        sp.run(["git", "init", "-q"], cwd=proj, check=True, env=env)
        # A supported-language file we will edit COSMETICALLY, and one we will
        # edit STRUCTURALLY.
        (proj / "cosmetic.py").write_text("def alpha():\n    return 1\n")
        (proj / "structural.py").write_text("def beta():\n    return 2\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "init"], cwd=proj, check=True, env=env)
        head = sp.run(["git", "rev-parse", "HEAD"], cwd=proj, capture_output=True,
                      text=True, env=env).stdout.strip()

        # Index the repo at this commit so there IS a baseline signature.
        from core.code_intel.parser import parse_file
        db = GraphStore(proj / "code_intel.db")
        db.set_meta("repo_root", str(proj))
        db.set_meta("last_indexed_commit", head)
        for f in ("cosmetic.py", "structural.py"):
            r = parse_file(proj / f, proj)
            if r.nodes:
                db.store_file_nodes_edges(f, r.nodes, r.edges, r.nodes[0].sha256 or "")
        db.rebuild_fts()
        db.close()

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        import core.code_intel as ci
        monkeypatch.setattr(ci, "load_project_graph",
                            lambda name: GraphStore(proj / "code_intel.db"))
        import jobs.handlers.code_intel_reindex as handler
        return handler, proj, env

    def test_cosmetic_skipped_structural_restored_nodes_conserved(self, tmp_path, monkeypatch):
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        from core.code_intel import grading
        handler, proj, env = self._init_repo(tmp_path, monkeypatch)

        # Snapshot the cosmetic file's stored nodes BEFORE the reindex.
        db0 = GraphStore(proj / "code_intel.db")
        cosmetic_nodes_before = {n["id"] for n in db0.get_nodes_by_file("cosmetic.py")}
        # alpha's stored line_start BEFORE = 1 (original position). This is the
        # DISCRIMINATING signal between skip and re-store: the cosmetic edit shifts
        # alpha to line 3. On SKIP the stored line_start stays 1 (the documented
        # drift); a re-store would update it to 3. Node ids alone canNOT tell skip
        # from re-store (both conserve ids, same signature) — so asserting the STALE
        # line_start is what proves the re-store was actually skipped. Without this,
        # the test is vacuous (mutation-verified: disabling the skip left it green).
        alpha_line_before = next(
            n["line_start"] for n in db0.get_nodes_by_file("cosmetic.py")
            if n["name"] == "alpha")
        db0.close()
        assert cosmetic_nodes_before, "baseline must have indexed cosmetic.py"
        assert alpha_line_before == 1

        # COSMETIC edit: add a comment + blank line (shifts alpha to line 3, same signature).
        (proj / "cosmetic.py").write_text(
            "# a new comment\n\ndef alpha():\n    return 1  # inline note\n")
        # STRUCTURAL edit: add a real new function (signature changes).
        (proj / "structural.py").write_text(
            "def beta():\n    return 2\n\ndef gamma():\n    return 3\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "mixed"], cwd=proj, check=True, env=env)

        result = handler.reindex_projects(full=False)
        p1 = next(r for r in result["projects"] if r["project"] == "P1")
        assert p1["status"] == "incremental"
        # At least one STRUCTURAL file → verdict is an update, not SKIP.
        assert p1["change_class"] in (grading.PARTIAL_UPDATE, grading.ARCHITECTURE_UPDATE,
                                       grading.FULL_UPDATE)

        db = GraphStore(proj / "code_intel.db")
        # COSMETIC file: nodes CONSERVED (never dropped)...
        cosmetic_nodes_after = {n["id"] for n in db.get_nodes_by_file("cosmetic.py")}
        assert cosmetic_nodes_after == cosmetic_nodes_before, "COSMETIC skip must conserve nodes"
        # ...AND the re-store was actually SKIPPED: alpha's stored line_start is
        # STILL 1 (stale), not updated to its new line 3. This is the non-vacuous
        # assertion — if the handler re-stored the COSMETIC file, line_start would
        # be 3 and this fails (mutation-verified: disabling the skip → RED here).
        alpha_line_after = next(
            n["line_start"] for n in db.get_nodes_by_file("cosmetic.py")
            if n["name"] == "alpha")
        assert alpha_line_after == alpha_line_before == 1, \
            "COSMETIC file must be SKIPPED (stale line_start), not re-stored"
        # STRUCTURAL file: the new function IS now in the graph.
        structural_ids = {n["name"] for n in db.get_nodes_by_file("structural.py")}
        assert "gamma" in structural_ids, "STRUCTURAL file must be re-stored with new symbol"
        # Aggregate verdict recorded to meta (real consumer signal).
        assert db.get_meta("last_change_class") == p1["change_class"]
        db.close()

    def test_all_cosmetic_yields_skip_verdict(self, tmp_path, monkeypatch):
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        from core.code_intel import grading
        handler, proj, env = self._init_repo(tmp_path, monkeypatch)

        # Only a comment change on ONE file → all-cosmetic changeset.
        (proj / "cosmetic.py").write_text("# just a comment\ndef alpha():\n    return 1\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "comment only"], cwd=proj, check=True, env=env)

        result = handler.reindex_projects(full=False)
        p1 = next(r for r in result["projects"] if r["project"] == "P1")
        assert p1["change_class"] == grading.SKIP, "all-cosmetic changeset → SKIP verdict"

        db = GraphStore(proj / "code_intel.db")
        assert db.get_meta("last_change_class") == grading.SKIP
        # Nodes still present (skip = leave untouched, never drop).
        assert {n["id"] for n in db.get_nodes_by_file("cosmetic.py")}
        db.close()

    def test_structural_file_emptied_of_symbols_purges_stale_nodes(self, tmp_path, monkeypatch):
        """Review HIGH: a previously-indexed file edited down to ZERO symbols
        (all defs removed) must have its stale nodes PURGED, not left in the graph.
        The old `if result.nodes:` guard leaked them (silent stale graph)."""
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        handler, proj, env = self._init_repo(tmp_path, monkeypatch)

        db0 = GraphStore(proj / "code_intel.db")
        assert {n["id"] for n in db0.get_nodes_by_file("structural.py")}, "baseline has beta"
        db0.close()

        # Edit structural.py down to comments only — zero symbols.
        (proj / "structural.py").write_text("# beta was here, now removed\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "gut structural"], cwd=proj, check=True, env=env)

        handler.reindex_projects(full=False)

        db = GraphStore(proj / "code_intel.db")
        # Stale nodes for structural.py must be GONE, not leaked.
        assert not db.get_nodes_by_file("structural.py"), \
            "STRUCTURAL file emptied of symbols must purge stale nodes (no phantom leak)"
        db.close()

    def test_none_skip_over_full_rebuild_baseline(self, tmp_path, monkeypatch):
        """Meta-review MED (run_4602932d): the sha256->file_hash fix (upsert_nodes)
        is only reachable via the FULL-REBUILD path (bulk_insert), which the other
        E2E tests bypass (they use store_file_nodes_edges). This test builds the
        baseline via bulk_insert (the real full-rebuild store), then a commit that
        does NOT change cosmetic.py's content — asserting it grades NONE (skipped).
        Without the fix, file_hash=NULL → byte_changed always True → never NONE."""
        import subprocess as sp
        from core.code_intel.graph_store import GraphStore
        from core.code_intel.parser import parse_file
        from core.code_intel import grading
        handler, proj, env = self._init_repo(tmp_path, monkeypatch)

        # Re-seed the graph via the FULL-REBUILD store path (bulk_insert), which is
        # where the sha256->file_hash fallback lives. _init_repo used
        # store_file_nodes_edges; overwrite with a bulk_insert baseline.
        baseline_head = sp.run(["git", "rev-parse", "HEAD"], cwd=proj, capture_output=True,
                                text=True, env=env).stdout.strip()
        db = GraphStore(proj / "code_intel.db")
        db.clear()
        parse_results = []
        for f in ("cosmetic.py", "structural.py"):
            r = parse_file(proj / f, proj)
            parse_results.append(r)
        db.bulk_insert(parse_results)  # full-rebuild path → exercises the fix
        # clear()+bulk_insert wipe graph_meta — restore freshness markers to the
        # CURRENT (pre-comment-edit) HEAD so the handler takes the INCREMENTAL branch
        # (not never-indexed → delegated full) and sees ONLY the upcoming comment commit.
        db.set_meta("repo_root", str(proj))
        db.set_meta("last_indexed_commit", baseline_head)
        # Prove the fix: file_hash is populated (not NULL) from sha256.
        stored = db.get_nodes_by_file("cosmetic.py")
        assert stored and stored[0]["file_hash"], "full-rebuild must persist file_hash (sha256 fallback)"
        db.close()

        # A commit that does NOT touch cosmetic.py's content (edit only structural.py
        # cosmetically) → cosmetic.py is not even in the changeset, structural.py is
        # a comment edit. Both should be NONE/COSMETIC → SKIP.
        (proj / "structural.py").write_text("# only a comment added\ndef beta():\n    return 2\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "comment only on structural"], cwd=proj, check=True, env=env)

        result = handler.reindex_projects(full=False)
        p1 = next(r for r in result["projects"] if r["project"] == "P1")
        # structural.py comment edit is COSMETIC (signature identical); cosmetic.py
        # untouched → all-skippable → SKIP verdict. This is the path fix #4 activates.
        assert p1["change_class"] == grading.SKIP, \
            "comment-only edit over a full-rebuild baseline must grade SKIP (fix #4 makes file_hash non-NULL)"


# ─── run_fe26ed6c: exporter spec_hash stamping + write→read loop closure ───

class TestSpecHashStampingLoop:
    """_stamp_spec_hashes (exporter) + detect_spec_details_staleness (freshness)
    close the write→read loop: a spec projected from a domain reads FRESH; a domain
    whose content then changes reads STALE. This is the whole point of the reshape —
    the stamp (write) and the detector (read) must agree by construction."""

    def _domain(self):
        return {"id": "domain:orders", "name": "orders", "summary": "order lifecycle",
                "entities": ["Order"], "complexity": "moderate"}

    def test_stamp_matches_skill_hash(self):
        from core.code_intel.json_exporter import _stamp_spec_hashes
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parents[3]
                                / "skills" / "s_ai-ready-repo" / "scripts"))
        from ai_ready_helpers import _spec_content_hash
        d = self._domain()
        doc = {"domains": [dict(d)], "flows": [], "steps": []}
        _stamp_spec_hashes(doc)
        assert doc["domains"][0]["spec_hash"] == _spec_content_hash(d, [], [])

    def test_write_then_read_loop_fresh(self, tmp_path):
        # project a spec from a domain, stamp the doc → detector says FRESH.
        import json, sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parents[3]
                                / "skills" / "s_ai-ready-repo" / "scripts"))
        from ai_ready_helpers import project_domain_skeleton
        from core.code_intel.json_exporter import _stamp_spec_hashes
        from core.code_intel.freshness import detect_spec_details_staleness
        d = self._domain()
        proj = tmp_path / "P"; (proj / "spec-details").mkdir(parents=True)
        (proj / "spec-details" / "orders.spec.md").write_text(
            project_domain_skeleton(d, [], []), encoding="utf-8")
        doc = {"domains": [dict(d)], "flows": [], "steps": []}
        _stamp_spec_hashes(doc)
        (proj / "code-intel.json").write_text(json.dumps(doc), encoding="utf-8")
        assert detect_spec_details_staleness(proj) == []  # projected+stamped = fresh

    def test_write_then_domain_change_reads_stale(self, tmp_path):
        # spec projected from OLD domain; doc re-stamped from CHANGED domain → STALE.
        import json, sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parents[3]
                                / "skills" / "s_ai-ready-repo" / "scripts"))
        from ai_ready_helpers import project_domain_skeleton
        from core.code_intel.json_exporter import _stamp_spec_hashes
        from core.code_intel.freshness import detect_spec_details_staleness
        d_old = self._domain()
        proj = tmp_path / "P"; (proj / "spec-details").mkdir(parents=True)
        (proj / "spec-details" / "orders.spec.md").write_text(
            project_domain_skeleton(d_old, [], []), encoding="utf-8")  # spec = OLD
        d_new = dict(d_old); d_new["summary"] = "CHANGED lifecycle"
        doc = {"domains": [d_new], "flows": [], "steps": []}
        _stamp_spec_hashes(doc)  # code-intel.json = NEW hash
        (proj / "code-intel.json").write_text(json.dumps(doc), encoding="utf-8")
        assert detect_spec_details_staleness(proj) == ["orders.spec.md"]
