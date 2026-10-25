"""repo_guidance also delivers a repo's pipeline lessons and its bound DDD (design
docs/design/2026-10-25-knowledge-delivery-gaps.md §3.2, part A / G2).

What is tested (§4 G2):
- the first touch of a repo that has a lessons file and is bound to a DDD project
  injects the lesson entries (newest first), the file's full path and the DDD doc paths;
- a second touch injects nothing; after the lessons file changes, the next touch does;
- a touch inside a run worktree finds the MAIN checkout's lessons and binding;
- too many lessons are cut (newest kept) and the full path is named;
- pending (待审) entries are counted, never shown as rules;
- repo_changes.bound_projects names the project of each bound root.
Mutation: drop the lessons part of _judge -> the first test goes red.
"""
import asyncio
import subprocess
from unittest.mock import MagicMock

import pytest

from core import repo_guidance as rg
from core.repo_changes import bound_projects as _bound_projects  # before the fixture patches it
from core.run_protocol import reflect_library


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    rg._STATE.clear()
    rg._root_cache.clear()
    rg._excluded_cache.clear()
    rg._bound_cache[:] = [0.0, {}]
    data = tmp_path / "appdata"
    ws = data / "SwarmWS"
    ws.mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: ws)
    monkeypatch.setattr("core.repo_changes.bound_projects", lambda: {})
    yield {"data": data, "ws": ws}
    rg._STATE.clear()


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "src.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "init"], check=True)
    return path


def _lesson(ws, repo, n, statement=None, dest="domain:P:TECH:Conventions", run="run_a"):
    out = reflect_library.write(ws, str(repo.resolve()),
                                {"id": f"L{n}", "statement": statement or f"lesson number {n}"},
                                dest, run)
    assert out and out.startswith("library:")


def _read(path, sid="s1"):
    hook = rg.create_repo_guidance_hook({"sdk_session_id": sid}, "PostToolUse")
    out = asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": str(path)},
                            "cwd": ""}, None, MagicMock()))
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def _bind(monkeypatch, ws, repo, project="P"):
    proj = ws / "Projects" / project
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "TECH.md").write_text("# tech\n")
    (proj / "PRODUCT.md").write_text("# product\n")
    monkeypatch.setattr("core.repo_changes.bound_projects",
                        lambda: {str(repo.resolve()): project})
    return proj


def test_first_touch_brings_lessons_and_ddd_paths_then_only_after_a_change(
        tmp_path, monkeypatch, _iso):
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    proj = _bind(monkeypatch, ws, repo)
    _lesson(ws, repo, 1, "always run the migration check")
    lessons = reflect_library.find(ws, str(repo.resolve()))
    assert lessons is not None and lessons.is_file()

    text = _read(repo / "src.py")
    assert "always run the migration check" in text
    assert str(lessons) in text
    assert str(proj / "TECH.md") in text and str(proj / "PRODUCT.md") in text
    assert "P" in text

    assert _read(repo / "src.py") == ""                       # once
    _lesson(ws, repo, 2, "never edit generated files", run="run_b")
    again = _read(repo / "src.py")
    assert "never edit generated files" in again


def test_a_run_worktree_finds_its_main_checkout(tmp_path, monkeypatch, _iso):
    ws, data = _iso["ws"], _iso["data"]
    repo = _repo(tmp_path / "proj")
    _bind(monkeypatch, ws, repo)
    _lesson(ws, repo, 1, "the main checkout lesson")
    wt = data / "worktrees" / "run_x" / "proj"
    wt.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "run/run_x",
                    str(wt)], check=True)
    text = _read(wt / "src.py")
    assert "the main checkout lesson" in text
    assert str(ws / "Projects" / "P" / "TECH.md") in text


def test_too_many_lessons_are_cut_newest_first_with_the_path(tmp_path, monkeypatch, _iso):
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    for n in range(60):
        _lesson(ws, repo, n, f"lesson {n:02d} " + "x" * 120, run=f"run_{n}")
    text = _read(repo / "src.py")
    assert "lesson 59" in text
    assert "lesson 00" not in text
    assert str(reflect_library.find(ws, str(repo.resolve()))) in text
    assert len(text) <= rg.MAX_BLOCK_CHARS


def test_pending_entries_are_counted_not_shown(tmp_path, _iso):
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    _lesson(ws, repo, 1, "agent must ask before X", dest="MEMORY")
    text = _read(repo / "src.py")
    assert "agent must ask before X" not in text
    assert "1" in text and "待审" in text


def test_no_lessons_and_unbound_repo_injects_nothing(tmp_path):
    repo = _repo(tmp_path / "proj")
    assert _read(repo / "src.py") == ""


def test_bound_projects_names_the_project(tmp_path, monkeypatch):
    from core import repo_changes
    repo = _repo(tmp_path / "r")
    monkeypatch.setattr(repo_changes, "_bindings",
                        lambda: {str(repo.resolve()): ("r", "Proj")})
    assert _bound_projects() == {str(repo.resolve()): "Proj"}
