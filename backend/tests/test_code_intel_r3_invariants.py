"""The invariants of code-intel r3 (design docs/design/2026-10-29-code-intel-unification.md
§0.6), each against real repos, real graphs and, where it is a process property, a real
second process.

1. A rebuild is one transaction: another process reading the graph while it is
   rewritten sees the old graph (never 0 nodes, never a missing ``repo_root``), and a
   writer killed or rolled back half-way leaves the old graph.
2. ``ensure_fresh`` answers every state it can be in: fresh (nothing written),
   updated, rebuilding (a child started, nothing parsed here), busy (another writer,
   or a bind), stale (no child allowed).
3. Ownership of a local checkout has one path: a person's Confirm. Product code calls
   ``local_roots.confirm`` from the Brain Hub endpoint only, and nothing else writes a
   ``confirmed`` state.
4. The daemon never parses: no module the daemon serves requests from calls a graph
   writer; the recall leg in the daemon stamps with one git call and writes nothing;
   the recall CLI (out of the daemon) brings graphs to HEAD first.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core.code_intel import CodeIntelDb
from core.code_intel.graph_store import GraphStore

BACKEND = Path(__file__).resolve().parents[1]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@e", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _commit(path, files, "init")
    return path


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def built(tmp_path):
    """A repo and its graph, built at HEAD: old.py::old is the old graph's symbol."""
    from core.code_intel.build import rebuild
    repo = _repo(tmp_path / "repo", {"old.py": "def old():\n    return 1\n"})
    db = tmp_path / "g.code_intel.db"
    g = GraphStore(db)
    rebuild(g, repo, head=_git(repo, "rev-parse", "HEAD"))
    g.close()
    for i in range(30):
        (repo / f"n{i}.py").write_text(f"def n{i}():\n    return {i}\n", encoding="utf-8")
    return repo, db


_READER = r"""
import json, sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=10)
nodes = conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0]
row = conn.execute("SELECT value FROM graph_meta WHERE key='repo_root'").fetchone()
ids = [r[0] for r in conn.execute("SELECT id FROM code_nodes ORDER BY id")]
print(json.dumps({"nodes": nodes, "repo_root": row[0] if row else None, "ids": ids}))
"""


def _read_in_another_process(db: Path) -> dict:
    import json
    out = subprocess.run([sys.executable, "-c", _READER, str(db)], capture_output=True,
                         text=True, timeout=60, check=True).stdout
    return json.loads(out)


# ── 1. one transaction ───────────────────────────────────────────────────────

def test_another_process_reading_mid_rebuild_sees_the_old_graph(built):
    """The rebuild is paused after ``clear()`` and the new rows are written but not
    committed; a second process reads the graph at exactly that point."""
    from core.code_intel.build import rebuild
    repo, db = built
    g = GraphStore(db)
    real = g.bulk_insert
    paused, resume = threading.Event(), threading.Event()

    def insert_then_pause(*a, **k):
        out = real(*a, **k)
        paused.set()
        assert resume.wait(60)
        return out
    g.bulk_insert = insert_then_pause
    done = []
    t = threading.Thread(target=lambda: done.append(rebuild(g, repo, head="new")))
    t.start()
    try:
        assert paused.wait(60)
        seen = _read_in_another_process(db)
        assert seen["ids"] == ["old.py::old"], seen
        assert seen["repo_root"] == str(repo.resolve())
    finally:
        resume.set()
        t.join(60)
    after = _read_in_another_process(db)
    assert "n29.py::n29" in after["ids"] and "old.py::old" in after["ids"]
    g.close()


_KILLED_WRITER = r"""
import sys, time
sys.path.insert(0, sys.argv[3])
from pathlib import Path
from core.code_intel.build import rebuild
from core.code_intel.graph_store import GraphStore
g = GraphStore(Path(sys.argv[1]))
real = g.bulk_insert
def insert_then_hang(*a, **k):
    real(*a, **k)
    Path(sys.argv[2]).write_text("inside")
    time.sleep(120)
g.bulk_insert = insert_then_hang
rebuild(g, Path(sys.argv[4]), head="never")
"""


def test_a_writer_killed_half_way_leaves_the_old_graph(built, tmp_path):
    repo, db = built
    marker = tmp_path / "inside.txt"
    child = subprocess.Popen([sys.executable, "-c", _KILLED_WRITER, str(db), str(marker),
                              str(BACKEND), str(repo)], cwd=BACKEND,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 60
        while not marker.exists():
            assert child.poll() is None, child.stderr.read().decode()[-2000:]
            assert time.monotonic() < deadline, "the writer never reached its transaction"
            time.sleep(0.05)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(30)
    finally:
        if child.poll() is None:
            child.kill()
    seen = _read_in_another_process(db)
    assert seen["ids"] == ["old.py::old"] and seen["repo_root"] == str(repo.resolve())
    g = GraphStore(db)
    try:
        assert g.get_meta("last_indexed_commit") != "never"
    finally:
        g.close()


def test_an_error_inside_the_rebuild_rolls_back_to_the_old_graph(built):
    from core.code_intel.build import rebuild
    repo, db = built
    (repo / "api.py").write_text('from fastapi import APIRouter\nrouter = APIRouter()\n\n'
                                 '@router.get("/x")\ndef x():\n    return 1\n', encoding="utf-8")
    g = GraphStore(db)
    try:
        g.insert_routes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
        with pytest.raises(RuntimeError, match="disk full"):
            rebuild(g, repo, head="new")
        assert [n["id"] for n in g.get_nodes_by_file("old.py")] == ["old.py::old"]
        assert g.get_nodes_by_file("n0.py") == []
        assert not g._conn.in_transaction
    finally:
        g.close()


def test_a_write_that_rolled_back_inside_fails_the_whole_transaction(built):
    """A store method that catches its own error rolls back; the transaction around
    it must not then commit the rest as if it had worked."""
    import sqlite3
    _repo_dir, db = built
    g = GraphStore(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="rolled back"):
            with g.transaction():
                g.clear()
                g._conn.rollback()                    # what a failing write method does
        assert [n["id"] for n in g.get_nodes_by_file("old.py")] == ["old.py::old"]
    finally:
        g.close()


# ── 2. every ensure_fresh state ──────────────────────────────────────────────

@pytest.fixture
def fresh_env(built, monkeypatch, tmp_path):
    import core.code_intel.build as build
    repo, db = built
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "thirty files")
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(build, "_sync_docs", lambda *a: None)
    g = GraphStore(db)
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo.resolve())
    yield build, repo, g, ref, spawned
    g.close()


def test_ensure_fresh_at_head_writes_nothing(fresh_env, tmp_path):
    build, repo, g, ref, spawned = fresh_env
    assert build.ensure_fresh(tmp_path, ref, g).state == "updated"
    marker = g.get_meta("last_indexed_commit")
    writes = []
    g._conn.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper()
                               .startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) else None)
    assert build.ensure_fresh(tmp_path, ref, g).state == "fresh"
    g._conn.set_trace_callback(None)
    assert writes == [] and spawned == [] and g.get_meta("last_indexed_commit") == marker


def test_ensure_fresh_is_busy_while_another_writer_holds_the_graph(fresh_env, tmp_path):
    build, repo, g, ref, spawned = fresh_env
    with build.write_lock(ref.db_path, wait=False) as held:
        assert held
        fr = build.ensure_fresh(tmp_path, ref, g)
    assert fr.state == "busy" and fr.changed
    assert g.get_nodes_by_file("n0.py") == [], "written while another writer held it"


def test_ensure_fresh_is_busy_while_the_repo_is_being_bound(fresh_env, tmp_path):
    from core.ddd_bindings import _bind_lock
    build, repo, g, ref, spawned = fresh_env
    with _bind_lock(repo, "repo"):
        assert build.ensure_fresh(tmp_path, ref, g).state == "busy"
    assert g.get_nodes_by_file("n0.py") == [] and spawned == []


def test_a_never_indexed_graph_starts_a_child_and_parses_nothing_here(tmp_path, monkeypatch):
    import core.code_intel.build as build
    repo = _repo(tmp_path / "repo", {"a.py": "def a():\n    return 1\n"})
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(build, "rebuild", lambda *a, **k: pytest.fail("parsed in-process"))
    g = GraphStore(tmp_path / "new.db")
    g.set_meta("repo_root", str(repo))
    try:
        ref = CodeIntelDb(db_path=tmp_path / "new.db", repo="repo", repo_root=repo)
        assert build.ensure_fresh(tmp_path / "P", ref, g).state == "rebuilding"
        assert spawned == [(("P",), {"repo": "repo", "full": True, "only_if_stale": True})]
        # without a child allowed (the survey) it answers stale
        assert build.ensure_fresh(tmp_path / "P", ref, g, spawn=False).state == "stale"
        assert len(spawned) == 1
    finally:
        g.close()


def test_a_rebuild_child_asked_while_current_does_nothing(built, monkeypatch):
    """``--if-stale``: two readers that both started a child do not rebuild twice."""
    import core.code_intel.build as build
    repo, db = built
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    ref = CodeIntelDb(db_path=db, repo="repo", repo_root=repo)
    assert build.build_graph(ref, only_if_stale=True) is not None
    monkeypatch.setattr(build, "rebuild", lambda *a, **k: pytest.fail("rebuilt a current graph"))
    assert build.build_graph(ref, only_if_stale=True) is None


# ── 3. ownership has one path ────────────────────────────────────────────────

def _product_grep(pattern: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(BACKEND), "grep", "--untracked", "-n", "-E", pattern, "--", "*.py",
         ":!tests/**", ":!**/tests/**", ":!**/test_*.py", ":!core/code_intel/local_roots.py"],
        capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def test_only_the_brain_hub_endpoint_confirms_a_local_root():
    callers = _product_grep(r"local_roots import .*[ ,]confirm([^a-z_]|$)|local_roots\.confirm\(")
    assert [c.split(":")[0] for c in callers] == ["routers/ddd_brain.py"], callers
    writes = _product_grep(r"""["']state["'] *[:=] *["']confirmed|local_roots.*CONFIRMED""")
    assert writes == [], writes


def test_propose_never_owns(tmp_path, monkeypatch):
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import propose
    proj = tmp_path / "Projects" / "Demo"
    proj.mkdir(parents=True)
    for source in ("agent", "tech-md", "confirmed"):
        repo = _repo(tmp_path / "code" / source, {"a.py": "x = 1\n"})
        assert propose("Demo", repo, source=source)["state"] == "pending"
    assert owned_repo_roots(proj) == []


# ── 4. the daemon never parses ───────────────────────────────────────────────

_WRITERS = frozenset({
    "rebuild", "build_graph", "ensure_graph", "update_files", "ensure_fresh",
    "index_code_mount", "parse_repo", "parse_repo_with_coverage", "incremental_update",
    "bulk_insert", "bind_repo", "bind_project", "store_file_nodes_edges"})


def _daemon_modules() -> list[Path]:
    return [*sorted((BACKEND / "routers").rglob("*.py")),
            *sorted((BACKEND / "hooks").rglob("*.py")), BACKEND / "main.py"]


def _writer_references(path: Path, root: Path = BACKEND) -> list[str]:
    """Every use of a graph writer's name in *path*'s code (a call, or the function
    handed to ``to_thread``/an executor), not in its comments or strings."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        name = (node.id if isinstance(node, ast.Name) else
                node.attr if isinstance(node, ast.Attribute) else
                None)
        if name in _WRITERS:
            found.append(f"{path.relative_to(root)}:{node.lineno}: {name}")
        if isinstance(node, ast.ImportFrom):
            found += [f"{path.relative_to(root)}:{node.lineno}: import {a.name}"
                      for a in node.names if a.name in _WRITERS]
    return found


def test_no_daemon_request_path_uses_a_graph_writer():
    """routers/, hooks/ and main.py run in the daemon: a write there parses on the
    chat loop's GIL. They start children instead (``spawn_build`` / ``spawn_child``)."""
    found = [hit for path in _daemon_modules() for hit in _writer_references(path)]
    assert found == [], found


def test_the_writer_scan_sees_a_call_and_a_function_handed_to_a_thread(tmp_path):
    sample = tmp_path / "m.py"
    sample.write_text("# ensure_graph in a comment\nimport asyncio\n"
                      "from core.code_intel.build import ensure_graph\n"
                      "async def f(g, p):\n    await asyncio.to_thread(ensure_graph, p)\n"
                      "    g.bulk_insert([])\n    return 'rebuild'\n", encoding="utf-8")
    hits = sorted(h.split(": ", 1)[1] for h in _writer_references(sample, tmp_path))
    assert hits == ["bulk_insert", "ensure_graph", "import ensure_graph"], hits


def test_the_daemon_recall_leg_stamps_with_one_git_call_and_writes_nothing(built, monkeypatch):
    import core.code_intel.build as build
    import core.code_intel.freshness as fr
    from core import recall_multi
    repo, db = built
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "behind")
    monkeypatch.setattr(build, "ensure_fresh", lambda *a, **k: pytest.fail("refreshed in the daemon"))
    calls = []
    real = fr._git
    monkeypatch.setattr(fr, "_git", lambda root, args, *a, **k: calls.append(args) or real(root, args, *a, **k))
    g = GraphStore(db)
    try:
        hits = recall_multi._graph_codeintel_hits("old", CodeIntelDb(db_path=db, repo="repo"), g, 5)
    finally:
        g.close()
    assert [h["id"] for h in hits] == ["old.py::old"] and hits[0]["graph_stale"] is True
    assert calls == [["rev-parse", "HEAD"]]


def test_the_recall_cli_refreshes_code_graphs(monkeypatch, capsys):
    import core.recall_multi as rm
    import scripts.context_recall_cli as cli
    seen = {}

    def fake_recall_all(query, **kw):
        seen.update(kw)
        return rm.BucketedRecall(query=query)
    monkeypatch.setattr(rm, "recall_all", fake_recall_all)
    cli.main(["--domains", "codeintel", "--query", "save", "--session-type", "desktop",
              "--project", "Demo"])
    assert seen.get("refresh_code") is True


def test_the_job_starts_a_child_for_a_graph_not_built_yet(tmp_path, monkeypatch, own_local_root):
    """A Confirm whose build child failed leaves an owned repo without a graph; the
    hourly job starts the child again instead of parsing within its own budget."""
    import core.code_intel.build as build
    from jobs.handlers.code_intel_reindex import reindex_projects
    repo = _repo(tmp_path / "repo", {"a.py": "def a():\n    return 1\n"})
    (tmp_path / "ws" / "Projects" / "Demo").mkdir(parents=True)
    own_local_root("Demo", repo)
    monkeypatch.setattr("config.get_swarmws", lambda: tmp_path / "ws")
    spawned = []
    monkeypatch.setattr(build, "spawn_build", lambda *a, **k: spawned.append((a, k)))
    monkeypatch.setattr(build, "rebuild", lambda *a, **k: pytest.fail("parsed in the job"))
    out = reindex_projects()
    assert spawned == [(("Demo",), {"repo": "repo", "only_if_stale": True})]
    assert [r["status"] for r in out["projects"]] == ["rebuilding"]


def test_a_name_starting_with_a_dash_reaches_the_child_as_a_value(monkeypatch):
    import core.code_intel.build as build
    seen = []
    monkeypatch.setattr(build, "spawn_child", lambda args: seen.append(args))
    build.spawn_build("-P", repo="--full", only_if_stale=True)
    args = seen[0]
    assert "--project=-P" in args and "--repo=--full" in args
    ns = __import__("argparse").ArgumentParser()
    ns.add_argument("--project"); ns.add_argument("--repo")
    ns.add_argument("--full", action="store_true"); ns.add_argument("--if-stale", action="store_true")
    parsed = ns.parse_args(args[2:])
    assert (parsed.project, parsed.repo, parsed.full) == ("-P", "--full", False)
