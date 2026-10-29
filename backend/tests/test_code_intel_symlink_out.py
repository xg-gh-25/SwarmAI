"""A repo may track a symlink to a file anywhere on the machine (``creds.py ->
~/.aws/...``). Its target is not the repo's content: no reader of repo files for the
code graph opens it — the full parse, the incremental update, route extraction, the
router-prefix pass and the domain-rule scan — and a file that becomes such a link
leaves the graph. Real repos, real symlinks, real graphs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.code_intel.graph_store import GraphStore

_SECRET = "def leaked_secret_function():\n    return 'AKIA-not-in-this-repo'\n"
_ROUTES = ('from fastapi import APIRouter\nrouter = APIRouter()\n\n'
           '@router.get("/outside")\ndef outside():\n    return 1\n')


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c", "user.name=t",
                    *args], check=True, capture_output=True)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    outside = tmp_path / "home" / "private"
    outside.mkdir(parents=True)
    (outside / "secret.py").write_text(_SECRET, encoding="utf-8")
    (outside / "api.py").write_text(_ROUTES, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    (repo / "inner.py").write_text("def inner():\n    return 2\n", encoding="utf-8")
    (repo / "alias.py").symlink_to("inner.py")                # inside: still read
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo, outside


def _symbols(g: GraphStore) -> set[str]:
    return {r[0] for r in g._conn.execute("SELECT name FROM code_nodes")}


def test_the_full_parse_never_reads_a_symlink_out_of_the_repo(tmp_path):
    from core.code_intel.build import rebuild
    repo, outside = _setup(tmp_path)
    (repo / "creds.py").symlink_to(outside / "secret.py")
    (repo / "api.py").symlink_to(outside / "api.py")
    g = GraphStore(tmp_path / "g.db")
    try:
        built = rebuild(g, repo, head="h")
        assert "leaked_secret_function" not in _symbols(g)
        assert {"app", "inner"} <= _symbols(g)
        assert g._conn.execute("SELECT COUNT(*) FROM code_routes").fetchone()[0] == 0
        holes = {h["ref"] for h in built.coverage_holes if h["kind"] == "outside_repo"}
        assert holes == {"creds.py", "api.py"}
    finally:
        g.close()


def test_a_file_that_becomes_a_symlink_out_leaves_the_graph_on_update(tmp_path):
    from core.code_intel.build import rebuild, update_files
    repo, outside = _setup(tmp_path)
    g = GraphStore(tmp_path / "g.db")
    try:
        rebuild(g, repo, head="h")
        assert "app" in _symbols(g)
        (repo / "app.py").unlink()
        (repo / "app.py").symlink_to(outside / "secret.py")
        (repo / "api.py").symlink_to(outside / "api.py")
        upd = update_files(g, repo, ["app.py", "api.py"])
        assert upd.complete
        assert "leaked_secret_function" not in _symbols(g) and "app" not in _symbols(g)
        assert g._conn.execute("SELECT COUNT(*) FROM code_routes").fetchone()[0] == 0
    finally:
        g.close()


def test_the_router_prefix_and_domain_rule_readers_refuse_it(tmp_path):
    from core.code_intel import resolve_router_prefixes
    from core.code_intel.parser import resolves_inside
    repo, outside = _setup(tmp_path)
    assert resolves_inside(repo, repo / "alias.py")
    (repo / "main.py").symlink_to(outside / "api.py")
    assert not resolves_inside(repo, repo / "main.py")
    g = GraphStore(tmp_path / "g.db")
    try:
        opened = []
        real_open = Path.read_text

        def spy(self, *a, **k):
            opened.append(self.name)
            return real_open(self, *a, **k)
        import unittest.mock as um
        with um.patch.object(Path, "read_text", spy):
            resolve_router_prefixes(g, repo)
        assert "main.py" not in opened
    finally:
        g.close()


def test_the_domain_rule_reader_returns_nothing_for_a_symlink_out(tmp_path):
    from core.code_intel.domain_rules import repo_source_reader
    repo, outside = _setup(tmp_path)
    (repo / "creds.py").symlink_to(outside / "secret.py")
    read = repo_source_reader(str(repo))
    assert read("creds.py") == ""
    assert "def inner" in read("alias.py") and "def app" in read("app.py")
