"""Code graph of a bound repo: visible, owned, kept correct incrementally, re-bound safely.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.8 (G1, G2, G7, G8), invariants
I6, I7 and I9. Each test names the defect it pins:

- I6  an incremental re-store kept the file's symbols but deleted every call INTO them
      from other files, and the file watcher's incremental_update never parsed at all;
- I7  a graph built by binding a repo lived beside the clone, where no reader looked,
      and its repo root was "not owned" unless TECH.md also declared a path;
- I9  re-binding deleted the clone even when it held uncommitted or unpushed work;
- G8  the clone source from bindings.yaml reached ``git clone`` as an option-capable
      argument with every transport allowed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.code_intel.graph_store import GraphStore


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")


def _write_bindings(project_dir: Path, repo: str, clone: str, worktree: str | None = None) -> None:
    wt = f"\n    worktree: {worktree}" if worktree else ""
    (project_dir / "bindings.yaml").write_text(
        "bindings:\n"
        f"  - repo: {repo}\n"
        "    kind: external\n"
        f"    clone: \"{clone}\"{wt}\n"
        "    delivery_contract:\n"
        "      remote_kind: github-pr\n"
        "      branch: main\n"
        "      review_path: github-pr\n"
        "      auto_send: manual-push\n",
        encoding="utf-8",
    )


def _two_file_graph(tmp_path: Path) -> tuple[GraphStore, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("from a import helper\n\ndef caller():\n    return helper()\n",
                               encoding="utf-8")
    from core.code_intel.parser import parse_repo
    graph = GraphStore(tmp_path / "g.db")
    graph.bulk_insert(parse_repo(repo), repo_root=repo)
    return graph, repo


def _callers(graph: GraphStore, target: str) -> list[str]:
    return [r[0] for r in graph._conn.execute(
        "SELECT source_id FROM code_edges WHERE target_id = ?", (target,)).fetchall()]


# ── I6: incremental re-store keeps callers and really parses ───────────────

class TestIncrementalKeepsCallers:
    def test_editing_a_file_keeps_calls_into_its_surviving_symbols(self, tmp_path):
        graph, repo = _two_file_graph(tmp_path)
        try:
            assert _callers(graph, "a.py::helper") == ["b.py::caller"]
            (repo / "a.py").write_text("def helper():\n    return 2\n\ndef extra():\n    return 3\n",
                                       encoding="utf-8")
            graph.incremental_update(repo, ["a.py"])
            assert _callers(graph, "a.py::helper") == ["b.py::caller"]
        finally:
            graph.close()

    def test_removing_a_symbol_drops_the_calls_into_it(self, tmp_path):
        graph, repo = _two_file_graph(tmp_path)
        try:
            (repo / "a.py").write_text("def renamed():\n    return 1\n", encoding="utf-8")
            graph.incremental_update(repo, ["a.py"])
            assert _callers(graph, "a.py::helper") == []
            ids = [n["id"] for n in graph.get_nodes_by_file("a.py")]
            assert ids == ["a.py::renamed"]
        finally:
            graph.close()

    def test_incremental_update_parses_new_symbols(self, tmp_path):
        """The watcher's entry point: a new function appears in the graph."""
        graph, repo = _two_file_graph(tmp_path)
        try:
            (repo / "b.py").write_text(
                "from a import helper\n\ndef caller():\n    return helper()\n\n"
                "def added():\n    return helper()\n", encoding="utf-8")
            out = graph.incremental_update(repo, ["b.py"])
            assert out["updated"] == ["b.py"]
            assert "b.py::added" in [n["id"] for n in graph.get_nodes_by_file("b.py")]
        finally:
            graph.close()

    def test_incremental_update_resolves_cross_file_calls(self, tmp_path):
        """A call stored incrementally points at the real node, not a dangling id."""
        graph, repo = _two_file_graph(tmp_path)
        try:
            (repo / "b.py").write_text(
                "from a import helper\n\ndef caller():\n    return helper()\n\n"
                "def added():\n    return helper()\n", encoding="utf-8")
            graph.incremental_update(repo, ["b.py"])
            assert sorted(_callers(graph, "a.py::helper")) == ["b.py::added", "b.py::caller"]
            dangling = graph._conn.execute(
                "SELECT COUNT(*) FROM code_edges e WHERE NOT EXISTS "
                "(SELECT 1 FROM code_nodes n WHERE n.id = e.target_id)").fetchone()[0]
            assert dangling == 0
        finally:
            graph.close()

    def test_unchanged_file_is_skipped_and_deleted_file_purged(self, tmp_path):
        graph, repo = _two_file_graph(tmp_path)
        try:
            out = graph.incremental_update(repo, ["a.py"])
            assert out["skipped"] == ["a.py"]
            (repo / "b.py").unlink()
            graph.incremental_update(repo, ["b.py"])
            assert graph.get_nodes_by_file("b.py") == []
            assert _callers(graph, "a.py::helper") == []
        finally:
            graph.close()


# ── I7: the bound graph is found and its clone is owned ──────────────────────

@pytest.fixture()
def bound_project(tmp_path, monkeypatch):
    """A project that binds one repo, with the db bind_repo would build beside the clone."""
    bindings_root = tmp_path / "bindings"
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings_root)
    projects = tmp_path / "Projects"
    project_dir = projects / "Demo"
    project_dir.mkdir(parents=True)
    _write_bindings(project_dir, "lib", "https://example.com/lib.git")
    clone = bindings_root / "lib"
    _init_repo(clone)
    db = bindings_root / "lib.code_intel.db"
    GraphStore(db).close()
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", projects)
    return project_dir, clone, db


class TestBoundGraphVisible:
    def test_resolver_finds_the_binding_db(self, bound_project):
        from core.code_intel import code_intel_db_for, code_intel_dbs
        project_dir, clone, db = bound_project
        dbs = code_intel_dbs(project_dir)
        assert [(d.db_path, d.repo, d.repo_root) for d in dbs] == [(db, "lib", clone.resolve())]
        assert code_intel_db_for(project_dir) == db

    def test_project_name_lookup_returns_the_binding_db(self, bound_project):
        from core.code_intel import get_code_intel_db_path, invalidate_cache, load_project_graph
        project_dir, _clone, db = bound_project
        assert get_code_intel_db_path("Demo") == db
        invalidate_cache("Demo")
        try:
            assert load_project_graph("Demo") is not None
        finally:
            invalidate_cache("Demo")

    def test_project_db_comes_first(self, bound_project):
        from core.code_intel import code_intel_dbs
        project_dir, _clone, db = bound_project
        own = project_dir / "code_intel.db"
        GraphStore(own).close()
        assert [d.db_path for d in code_intel_dbs(project_dir)] == [own, db]

    def test_binding_clone_is_owned_without_a_tech_md_path(self, bound_project):
        from core.code_intel import repo_root_is_owned
        project_dir, clone, _db = bound_project
        assert not (project_dir / "TECH.md").exists()
        assert repo_root_is_owned(project_dir, str(clone))
        assert not repo_root_is_owned(project_dir, str(clone.parent))

    def test_edit_inside_a_bound_clone_maps_to_the_project(self, bound_project):
        """The edit hook's repo→project map knows the bound clone (no TECH.md path)."""
        import core.code_intel as ci
        _project_dir, clone, _db = bound_project
        (clone / "m.py").write_text("x = 1\n", encoding="utf-8")
        ci.invalidate_cache()
        try:
            assert ci.detect_project_from_path(str(clone / "m.py")) == "Demo"
        finally:
            ci.invalidate_cache()

    def test_internal_binding_contributes_nothing(self, tmp_path, monkeypatch):
        from core.code_intel import code_intel_dbs, owned_repo_roots
        bindings_root = tmp_path / "bindings"
        monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings_root)
        project_dir = tmp_path / "P"
        project_dir.mkdir()
        (project_dir / "bindings.yaml").write_text(
            "bindings:\n  - repo: svc\n    kind: internal\n    clone: \"brazil ws create\"\n"
            "    delivery_contract:\n      remote_kind: code-amazon-cr\n      branch: main\n"
            "      review_path: cr\n      auto_send: manual\n", encoding="utf-8")
        bindings_root.mkdir()
        (bindings_root / "svc").mkdir()
        GraphStore(bindings_root / "svc.code_intel.db").close()
        assert code_intel_dbs(project_dir) == []
        assert owned_repo_roots(project_dir) == []


# ── I9: re-binding never deletes unsaved work ────────────────────────────────

class TestRebindPreserves:
    def test_dirty_worktree_is_renamed_not_deleted(self, tmp_path):
        from core.ddd_bindings import _clear_worktree_for_reclone
        wt = tmp_path / "bindings" / "lib"
        _init_repo(wt)
        (wt / "notes.txt").write_text("my local work\n", encoding="utf-8")
        preserved = _clear_worktree_for_reclone(wt)
        assert not wt.exists()
        assert preserved is not None and preserved.name.startswith("lib.preserved-")
        assert (preserved / "notes.txt").read_text(encoding="utf-8") == "my local work\n"

    def test_unpushed_commit_is_preserved(self, tmp_path):
        from core.ddd_bindings import _clear_worktree_for_reclone
        wt = tmp_path / "bindings" / "lib"
        _init_repo(wt)
        (wt / "x.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "x.py")
        _git(wt, "commit", "-q", "-m", "local only")
        preserved = _clear_worktree_for_reclone(wt)
        assert preserved is not None and (preserved / "x.py").exists()

    def test_clean_pushed_worktree_is_removed(self, tmp_path):
        from core.ddd_bindings import _clear_worktree_for_reclone
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        wt = tmp_path / "bindings" / "lib"
        _init_repo(wt)
        (wt / "x.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "x.py")
        _git(wt, "commit", "-q", "-m", "c")
        _git(wt, "remote", "add", "origin", str(remote))
        _git(wt, "push", "-q", "origin", "main")
        assert _clear_worktree_for_reclone(wt) is None
        assert not wt.exists()

    def test_non_git_directory_with_content_is_preserved(self, tmp_path):
        from core.ddd_bindings import _clear_worktree_for_reclone
        wt = tmp_path / "bindings" / "lib"
        wt.mkdir(parents=True)
        (wt / "file").write_text("?", encoding="utf-8")
        preserved = _clear_worktree_for_reclone(wt)
        assert preserved is not None and (preserved / "file").exists()


# ── G8: the clone source cannot become a git option or an arbitrary transport ──

class TestCloneHardening:
    def test_git_clone_ends_options_and_limits_protocols(self, tmp_path):
        from core.git_sync_engine import GitSyncEngine
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
            return subprocess.CompletedProcess(cmd, 1, "", "")

        with patch("core.git_sync_engine.subprocess.run", side_effect=fake_run):
            GitSyncEngine(workspace_dir=tmp_path).git_clone("--upload-pack=touch /tmp/x", tmp_path / "t")
        cmd = seen["cmd"]
        assert cmd[:3] == ["git", "clone", "--"]
        assert cmd[3] == "--upload-pack=touch /tmp/x"
        assert seen["env"].get("GIT_ALLOW_PROTOCOL") == "https:ssh:file"

    def test_bind_refuses_a_protected_local_clone_source(self, tmp_path):
        from core.ddd_bindings import Binding, bind_repo
        binding = Binding.model_validate({
            "repo": "keys", "kind": "external", "clone": str(Path.home() / ".ssh"),
            "delivery_contract": {"remote_kind": "github-pr", "branch": "main",
                                  "review_path": "pr", "auto_send": "manual"},
        })
        with patch("core.git_sync_engine.GitSyncEngine.git_clone") as clone:
            with pytest.raises(ValueError, match="protected system path"):
                bind_repo(binding, tmp_path / "bindings")
        clone.assert_not_called()


# ── readers wired to the resolver ────────────────────────────────────────────

class TestReadersSeeTheBoundGraph:
    def test_brain_hub_reports_code_intel_for_a_bound_repo(self, bound_project):
        from routers.ddd_brain import _brain_detail
        project_dir, _clone, _db = bound_project
        assert not (project_dir / "code_intel.db").exists()
        assert _brain_detail(project_dir)["hasCodeIntel"] is True

    def test_manual_reindex_replaces_the_graph(self, bound_project):
        """POST /reindex used to bulk_insert on top of the old rows: a deleted file's
        symbols stayed forever. It now clears and re-parses the owned clone."""
        import routers.code_intel as mod
        project_dir, clone, db = bound_project
        (clone / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
        graph = GraphStore(db)
        try:
            graph.upsert_nodes([{
                "id": "gone.py::gone", "file_path": "gone.py", "node_type": "function",
                "name": "gone", "line_start": 1, "line_end": 2, "language": "python"}])
            graph.set_meta("repo_root", str(clone))
        finally:
            graph.close()
        from core.code_intel import invalidate_cache
        invalidate_cache("Demo")
        try:
            mod._run_reindex("Demo")
        finally:
            invalidate_cache("Demo")
        graph = GraphStore(db)
        try:
            ids = {r[0] for r in graph._conn.execute("SELECT id FROM code_nodes").fetchall()}
            assert "keep.py::keep" in ids
            assert "gone.py::gone" not in ids
        finally:
            graph.close()


# ── review round 1 fixes ─────────────────────────────────────────────────────

def _binding(repo: str, clone: str = "https://example.com/x.git", worktree: str | None = None):
    from core.ddd_bindings import Binding
    data = {"repo": repo, "kind": "external", "clone": clone,
            "delivery_contract": {"remote_kind": "github-pr", "branch": "main",
                                  "review_path": "pr", "auto_send": "manual"}}
    if worktree is not None:
        data["worktree"] = worktree
    return Binding.model_validate(data)


class TestWorktreeConfinement:
    @pytest.mark.parametrize("worktree", ["evil/sub", ".", "a/../.", "a/b/c"])
    def test_worktree_must_be_one_directory_under_the_root(self, tmp_path, worktree):
        """Nested in another clone (its committed files can be a git dir whose config
        runs a command) or the root itself (a re-bind would move every clone)."""
        from core.ddd_bindings import binding_worktree
        with pytest.raises(ValueError, match="escapes the bindings root"):
            binding_worktree(_binding("lib", worktree=worktree), tmp_path / "bindings")

    def test_a_plain_name_and_its_absolute_form_are_accepted(self, tmp_path):
        from core.ddd_bindings import binding_worktree
        root = tmp_path / "bindings"
        root.mkdir()
        assert binding_worktree(_binding("lib"), root) == (root / "lib").resolve()
        absolute = str((root / "other").resolve())
        assert binding_worktree(_binding("lib", worktree=absolute), root) == Path(absolute)


class TestCloneSourceValidation:
    @pytest.mark.parametrize("clone", [
        "file://" + str(Path.home() / ".ssh"),
        "FILE://" + str(Path.home() / ".ssh"),
        "ext::sh -c touch% /tmp/pwned",
        "fd::17",
        "git://example.com/x.git",
        "http://example.com/x.git",
    ])
    def test_refused_before_git_runs(self, tmp_path, clone):
        from core.ddd_bindings import bind_repo
        with patch("core.git_sync_engine.GitSyncEngine.git_clone") as git_clone:
            with pytest.raises(ValueError):
                bind_repo(_binding("lib", clone=clone), tmp_path / "bindings")
        git_clone.assert_not_called()

    @pytest.mark.parametrize("clone", [
        "https://github.com/pallets/itsdangerous.git",
        "ssh://git@github.com/a/b.git",
        "git@github.com:a/b.git",
    ])
    def test_remote_sources_pass(self, clone):
        from core.ddd_bindings import _validate_clone_source
        _validate_clone_source(_binding("lib", clone=clone))

    def test_a_plain_local_repo_passes(self, tmp_path):
        from core.ddd_bindings import _validate_clone_source
        src = tmp_path / "src"
        _init_repo(src)
        _validate_clone_source(_binding("lib", clone=str(src)))
        _validate_clone_source(_binding("lib", clone="file://" + str(src)))


class TestUnsavedWorkCoversEverythingLocal:
    def _pushed(self, tmp_path) -> Path:
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        wt = tmp_path / "bindings" / "lib"
        _init_repo(wt)
        (wt / ".gitignore").write_text("*.env\n", encoding="utf-8")
        (wt / "x.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", "c")
        _git(wt, "remote", "add", "origin", str(remote))
        _git(wt, "push", "-q", "origin", "main")
        _git(wt, "fetch", "-q", "origin")
        return wt

    def test_ignored_file_counts(self, tmp_path):
        from core.ddd_bindings import _worktree_has_unsaved_work
        wt = self._pushed(tmp_path)
        assert not _worktree_has_unsaved_work(wt)
        (wt / "local.env").write_text("TOKEN=mine\n", encoding="utf-8")
        assert _worktree_has_unsaved_work(wt)

    def test_stash_counts(self, tmp_path):
        from core.ddd_bindings import _worktree_has_unsaved_work
        wt = self._pushed(tmp_path)
        (wt / "x.py").write_text("x = 2\n", encoding="utf-8")
        _git(wt, "stash", "-q")
        assert _git(wt, "status", "--porcelain") == ""
        assert _worktree_has_unsaved_work(wt)

    def test_detached_commit_counts(self, tmp_path):
        from core.ddd_bindings import _worktree_has_unsaved_work
        wt = self._pushed(tmp_path)
        _git(wt, "checkout", "-q", "--detach")
        (wt / "y.py").write_text("y = 1\n", encoding="utf-8")
        _git(wt, "add", "y.py")
        _git(wt, "commit", "-q", "-m", "detached")
        _git(wt, "checkout", "-q", "main")
        assert _worktree_has_unsaved_work(wt)

    def test_commit_reset_away_counts(self, tmp_path):
        from core.ddd_bindings import _worktree_has_unsaved_work
        wt = self._pushed(tmp_path)
        (wt / "y.py").write_text("y = 1\n", encoding="utf-8")
        _git(wt, "add", "y.py")
        _git(wt, "commit", "-q", "-m", "local")
        _git(wt, "reset", "-q", "--hard", "HEAD~1")
        assert _worktree_has_unsaved_work(wt)

    def test_repo_config_cannot_run_a_command(self, tmp_path):
        """core.fsmonitor in the checkout's own config would run on `git status`."""
        from core.ddd_bindings import _worktree_has_unsaved_work
        wt = self._pushed(tmp_path)
        marker = tmp_path / "pwned"
        _git(wt, "config", "core.fsmonitor", f"touch {marker}; false")
        _worktree_has_unsaved_work(wt)
        assert not marker.exists()

    def test_linked_worktree_is_preserved(self, tmp_path):
        """A checkout whose .git is a file (a linked worktree the user made there) was
        not made by our clone: keep it, even when git calls it clean and pushed."""
        from core.ddd_bindings import _worktree_has_unsaved_work
        main = self._pushed(tmp_path)
        linked = tmp_path / "bindings" / "linked"
        _git(main, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
        assert (linked / ".git").is_file()
        assert _worktree_has_unsaved_work(linked)


class TestOwnershipIsNotAFreePath:
    def test_governed_asset_path_is_not_owned(self, tmp_path, monkeypatch):
        """A source_workspace in bindings.yaml must not make an arbitrary repo indexable."""
        from core.code_intel import owned_repo_roots, repo_root_is_owned
        monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: tmp_path / "bindings")
        other = tmp_path / "someone_else"
        _init_repo(other)
        project_dir = tmp_path / "P"
        project_dir.mkdir()
        (project_dir / "bindings.yaml").write_text(
            "governed_assets:\n  - kind: code-repo\n"
            f"    source_workspace: {other}\n"
            "bindings: []\n", encoding="utf-8")
        assert owned_repo_roots(project_dir) == []
        assert not repo_root_is_owned(project_dir, str(other))


class TestBindLock:
    def test_second_bind_of_the_same_worktree_is_refused(self, tmp_path):
        from core.ddd_bindings import _bind_lock, bind_in_progress
        wt = tmp_path / "bindings" / "lib"
        wt.parent.mkdir(parents=True)
        assert not bind_in_progress(wt)
        with _bind_lock(wt, "lib"):
            assert bind_in_progress(wt)
            with pytest.raises(RuntimeError, match="another bind"):
                with _bind_lock(wt, "lib"):
                    pass
        assert not bind_in_progress(wt)

    def test_watcher_skips_while_a_bind_runs(self, tmp_path):
        import asyncio
        from core.code_intel.watcher import CodeIntelWatcher
        from core.ddd_bindings import _bind_lock
        wt = tmp_path / "bindings" / "lib"
        wt.mkdir(parents=True)
        (wt / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        graph = GraphStore(tmp_path / "g.db")
        try:
            watcher = CodeIntelWatcher("NoSuchProject", wt, graph)
            with _bind_lock(wt, "lib"):
                asyncio.run(watcher._trigger_incremental([wt / "a.py"]))
            assert graph.get_nodes_by_file("a.py") == []
            asyncio.run(watcher._trigger_incremental([wt / "a.py"]))
            assert [n["id"] for n in graph.get_nodes_by_file("a.py")] == ["a.py::a"]
        finally:
            graph.close()

    def test_watcher_uses_the_current_store_after_invalidation(self, bound_project):
        """invalidate_cache closes the cached store; the watcher must not keep the closed one."""
        import asyncio
        from core.code_intel import invalidate_cache, load_project_graph
        from core.code_intel.watcher import CodeIntelWatcher
        _project_dir, clone, _db = bound_project
        invalidate_cache("Demo")
        try:
            first = load_project_graph("Demo")
            watcher = CodeIntelWatcher("Demo", clone, first)
            invalidate_cache("Demo")  # closes `first`
            (clone / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
            asyncio.run(watcher._trigger_incremental([clone / "a.py"]))
            current = load_project_graph("Demo")
            assert [n["id"] for n in current.get_nodes_by_file("a.py")] == ["a.py::a"]
        finally:
            invalidate_cache("Demo")


class TestGraphWriteAtomicity:
    def test_a_reader_never_sees_a_file_half_replaced(self, tmp_path):
        """Between deleting a file's rows and re-inserting them, another thread on the
        same connection must wait, not read zero symbols."""
        import threading
        import core.code_intel.graph_store as gs
        graph, repo = _two_file_graph(tmp_path)
        try:
            from core.code_intel.parser import parse_file
            result = parse_file(repo / "a.py", repo)
            seen: list = []

            def reader():
                seen.append(len(graph.get_nodes_by_file("a.py")))

            real = gs._sanitize_name
            started = []

            def hook(name):
                if not started:  # first call happens after the delete, before inserts
                    started.append(1)
                    t = threading.Thread(target=reader)
                    t.start()
                    t.join(timeout=0.3)
                    started.append(t)
                return real(name)

            with patch.object(gs, "_sanitize_name", side_effect=hook):
                graph.store_file_nodes_edges("a.py", result.nodes, result.edges, "h")
            started[1].join(timeout=5)
            assert seen == [1], f"reader saw {seen} mid-replace"
        finally:
            graph.close()

    def test_incremental_prune_leaves_other_files_edges_alone(self, tmp_path):
        """A full rebuild on another connection commits file by file; an edge it stored
        whose target file has not landed yet must survive an incremental prune."""
        graph, repo = _two_file_graph(tmp_path)
        try:
            graph.upsert_edges([{"source_id": "b.py::caller", "target_id": "later.py::x",
                                 "edge_type": "calls"}])
            (repo / "a.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
            graph.incremental_update(repo, ["a.py"])
            targets = [r[0] for r in graph._conn.execute(
                "SELECT target_id FROM code_edges WHERE source_id = 'b.py::caller'").fetchall()]
            assert "later.py::x" in targets
        finally:
            graph.close()

    def test_a_failed_batch_does_not_mark_the_graph_fresh(self, tmp_path):
        graph, repo = _two_file_graph(tmp_path)
        try:
            (repo / "a.py").write_text("def helper():\n    return 3\n", encoding="utf-8")
            with patch("core.code_intel.parser.parse_file", side_effect=RuntimeError("boom")):
                result = graph.incremental_update(repo, ["a.py"])
            assert result["failed"] == ["a.py"]
            assert graph.get_meta("last_incremental_update") is None
        finally:
            graph.close()

    def test_a_locked_store_fails_one_file_not_the_batch(self, tmp_path):
        import sqlite3
        graph, repo = _two_file_graph(tmp_path)
        try:
            (repo / "a.py").write_text("def helper():\n    return 3\n", encoding="utf-8")
            (repo / "b.py").write_text("def caller():\n    return 4\n", encoding="utf-8")
            real = graph.store_file_nodes_edges

            def flaky(fpath, *a, **k):
                if fpath == "a.py":
                    raise sqlite3.OperationalError("database is locked")
                return real(fpath, *a, **k)

            with patch.object(graph, "store_file_nodes_edges", side_effect=flaky):
                result = graph.incremental_update(repo, ["a.py", "b.py"])
            assert result["failed"] == ["a.py"] and result["updated"] == ["b.py"]
        finally:
            graph.close()


class TestRouterNeverMixesRepos:
    def test_binding_db_with_missing_clone_is_not_filled_from_another_repo(
            self, bound_project, tmp_path):
        import routers.code_intel as mod
        from core.code_intel import invalidate_cache
        project_dir, clone, db = bound_project
        other = tmp_path / "other_repo"
        other.mkdir()
        (other / "o.py").write_text("def other():\n    return 1\n", encoding="utf-8")
        (project_dir / "TECH.md").write_text(f"# TECH\n\n**Local:** `{other}`\n", encoding="utf-8")
        import shutil
        shutil.rmtree(clone)
        invalidate_cache("Demo")
        try:
            mod._run_reindex("Demo")
        finally:
            invalidate_cache("Demo")
        graph = GraphStore(db)
        try:
            assert graph.get_nodes_by_file("o.py") == []
        finally:
            graph.close()


class TestIncrementalWritersResolve:
    def test_reindex_job_resolves_a_cross_file_call_it_stored(self, tmp_path, monkeypatch):
        import subprocess as sp
        from core.code_intel.tests.test_freshness import TestGradedIncrementalE2E as _Fixture
        handler, proj, env = _Fixture()._init_repo(tmp_path, monkeypatch)
        monkeypatch.setattr("config.get_swarmws", lambda: proj.parent.parent)
        (proj / "structural.py").write_text(
            "from cosmetic import alpha\n\ndef beta():\n    return alpha()\n")
        sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
        sp.run(["git", "commit", "-q", "-m", "call"], cwd=proj, check=True, env=env)
        handler.reindex_projects(full=False)
        graph = GraphStore(proj / "code_intel.db")
        try:
            targets = [r[0] for r in graph._conn.execute(
                "SELECT target_id FROM code_edges WHERE source_id = 'structural.py::beta'"
            ).fetchall()]
            assert "cosmetic.py::alpha" in targets
        finally:
            graph.close()

    def test_health_hook_drops_a_removed_symbol(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        import core.code_intel as ci
        import core.code_intel.freshness as fr
        from hooks.context_health_hook import ContextHealthHook
        proj = tmp_path / "Projects" / "P"
        repo = proj / "repo"
        repo.mkdir(parents=True)
        (repo / "a.py").write_text("def keep():\n    return 1\n\ndef drop():\n    return 2\n",
                                   encoding="utf-8")
        (proj / "TECH.md").write_text(f"# TECH\n\n**Local:** `{repo}`\n", encoding="utf-8")
        from core.code_intel.parser import parse_repo
        graph = GraphStore(proj / "code_intel.db")
        graph.bulk_insert(parse_repo(repo), repo_root=repo)
        try:
            (repo / "a.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
            stale = MagicMock(stale=True, suggest_full_rebuild=False, commits_behind=1,
                              changed_files=["a.py"], current_head=None)
            monkeypatch.setattr(ci, "load_project_graph", lambda name: graph)
            monkeypatch.setattr(fr, "check_freshness", lambda g: stale)
            ContextHealthHook()._refresh_code_intel(tmp_path)
            assert [n["id"] for n in graph.get_nodes_by_file("a.py")] == ["a.py::keep"]
        finally:
            graph.close()


# ── review round 2 fixes ─────────────────────────────────────────────────────

class TestFailedWriteLeavesNoOpenTransaction:
    def test_a_delete_that_fails_part_way_is_rolled_back(self, tmp_path):
        """remove_file fails after deleting edges: the edges must come back and no
        transaction may stay open on the shared connection for the next write to join."""
        graph, repo = _two_file_graph(tmp_path)
        try:
            before = sorted(graph._conn.execute(
                "SELECT source_id, target_id FROM code_edges").fetchall())
            assert before
            graph._conn.execute(
                "CREATE TRIGGER no_del BEFORE DELETE ON code_nodes "
                "WHEN old.file_path = 'a.py' BEGIN SELECT RAISE(ABORT, 'boom'); END")
            graph._conn.commit()
            (repo / "a.py").unlink()
            result = graph.incremental_update(repo, ["a.py"])
            assert result["failed"] == ["a.py"]
            assert not graph._conn.in_transaction
            graph.set_meta("probe", "1")  # any later commit
            after = sorted(graph._conn.execute(
                "SELECT source_id, target_id FROM code_edges").fetchall())
            assert after == before
        finally:
            graph.close()

    def test_resolve_only_touches_the_restored_files(self, tmp_path):
        """A bare call stored by a rebuild still in progress elsewhere stays bare."""
        graph, repo = _two_file_graph(tmp_path)
        try:
            graph.upsert_nodes([{
                "id": "c.py::other", "file_path": "c.py", "node_type": "function",
                "name": "other", "line_start": 1, "line_end": 2, "language": "python"}])
            graph.upsert_edges([{"source_id": "c.py::other", "target_id": "helper",
                                 "edge_type": "calls"}])
            (repo / "a.py").write_text("def helper():\n    return 5\n", encoding="utf-8")
            graph.incremental_update(repo, ["a.py"])
            targets = [r[0] for r in graph._conn.execute(
                "SELECT target_id FROM code_edges WHERE source_id = 'c.py::other'").fetchall()]
            assert targets == ["helper"]
        finally:
            graph.close()


class TestRouterReindexTiming:
    def _commit(self, repo: Path, name: str) -> str:
        (repo / name).write_text(f"def {name[:-3]}():\n    return 1\n", encoding="utf-8")
        _git(repo, "add", name)
        _git(repo, "commit", "-q", "-m", name)
        return _git(repo, "rev-parse", "HEAD").strip()

    def test_skips_while_the_clone_is_being_bound(self, bound_project):
        import routers.code_intel as mod
        from core.code_intel import invalidate_cache
        from core.ddd_bindings import _bind_lock
        project_dir, clone, db = bound_project
        self._commit(clone, "keep.py")
        invalidate_cache("Demo")
        try:
            with _bind_lock(clone, "lib"):
                mod._run_reindex("Demo")
        finally:
            invalidate_cache("Demo")
        graph = GraphStore(db)
        try:
            assert graph.get_nodes_by_file("keep.py") == []
        finally:
            graph.close()

    def test_head_is_read_before_the_parse(self, bound_project):
        import routers.code_intel as mod
        from core.code_intel import invalidate_cache
        from core.code_intel.parser import parse_repo as real_parse
        project_dir, clone, db = bound_project
        head_before = self._commit(clone, "keep.py")

        def parse_then_commit(root):
            out = real_parse(root)
            self._commit(clone, "late.py")  # lands while the rebuild runs
            return out

        invalidate_cache("Demo")
        try:
            with patch("core.code_intel.parser.parse_repo", side_effect=parse_then_commit):
                mod._run_reindex("Demo")
        finally:
            invalidate_cache("Demo")
        graph = GraphStore(db)
        try:
            assert graph.get_meta("last_indexed_commit") == head_before
        finally:
            graph.close()


class TestReservedNamesAndLockFile:
    @pytest.mark.parametrize("worktree", ["lib.bind.lock", "x.code_intel.db", "lib.preserved-1"])
    def test_reserved_names_refused(self, tmp_path, worktree):
        from core.ddd_bindings import binding_worktree
        with pytest.raises(ValueError, match="reserved"):
            binding_worktree(_binding("lib", worktree=worktree), tmp_path / "bindings")

    def test_lock_file_symlink_is_not_followed(self, tmp_path):
        from core.ddd_bindings import _bind_lock
        root = tmp_path / "bindings"
        root.mkdir()
        target = tmp_path / "elsewhere"
        (root / "lib.bind.lock").symlink_to(target)
        with pytest.raises(OSError):
            with _bind_lock(root / "lib", "lib"):
                pass
        assert not target.exists()


class TestWriteLockedRollsBack:
    def test_outermost_failing_write_rolls_back(self, tmp_path):
        from core.code_intel.graph_store import _write_locked

        class Store(GraphStore):
            @_write_locked
            def half_write(self):
                self._conn.execute("DELETE FROM code_edges")
                raise RuntimeError("boom")

        graph, _repo = _two_file_graph(tmp_path)
        graph.close()
        store = Store(tmp_path / "g.db")
        try:
            n = store._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
            assert n
            with pytest.raises(RuntimeError):
                store.half_write()
            assert not store._conn.in_transaction
            assert store._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0] == n
        finally:
            store.close()

    def test_a_prune_that_fails_part_way_leaves_no_open_transaction(self, tmp_path, monkeypatch):
        """The prune swallows its error; a chunk already deleted must not stay in an
        open transaction that the next write would join and commit."""
        import core.code_intel.graph_store as gs
        monkeypatch.setattr(gs, "_BATCH_SIZE", 1)
        graph, _repo = _two_file_graph(tmp_path)
        try:
            graph.upsert_nodes([{
                "id": "x.py::f", "file_path": "x.py", "node_type": "function",
                "name": "f", "line_start": 1, "line_end": 2, "language": "python"}])
            graph.upsert_edges([
                {"source_id": "x.py::f", "target_id": "nowhere1", "edge_type": "calls"},
                {"source_id": "b.py::caller", "target_id": "nowhere2", "edge_type": "calls"}])
            graph._conn.execute(
                "CREATE TRIGGER no_prune BEFORE DELETE ON code_edges "
                "WHEN old.target_id = 'nowhere2' BEGIN SELECT RAISE(ABORT, 'boom'); END")
            graph._conn.commit()
            graph.resolve_and_prune(files=["x.py", "b.py"])
            assert not graph._conn.in_transaction
        finally:
            graph.close()
