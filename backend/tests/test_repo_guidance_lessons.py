"""repo_guidance also delivers a repo's pipeline lessons and its bound DDD (design
docs/design/2026-10-25-knowledge-delivery-gaps.md §3.2, part A / G2).

What is tested (§4 G2):
- the first touch of a repo that has a lessons file and is bound to a DDD project
  injects the lesson entries (newest first), the file's full path and where its DDD is;
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
    # the bound DDD is one line: where it is and how to look things up (knowledge
    # addressing §3), ahead of the lessons so the lessons block cannot crowd it out
    assert f"its DDD is at {proj}" in text and "knowledge find --project P" in text
    assert text.index("[Project DDD]") < text.index("always run the migration check")

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
    assert f"its DDD is at {ws / 'Projects' / 'P'}" in text


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



def test_the_ddd_line_is_not_marked_seen_when_it_did_not_fit(tmp_path, monkeypatch, _iso):
    """A DDD line that did not fit the block must come back on the next touch: it used
    to be marked seen before the fit check, and was then never shown."""
    from core import repo_guidance
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    _bind(monkeypatch, ws, repo)
    monkeypatch.setattr(repo_guidance, "MAX_BLOCK_CHARS", 620)  # the header fits, the DDD line not
    first = _read(repo / "src.py")
    assert "Repository guidance" in first and "[Project DDD]" not in first
    monkeypatch.setattr(repo_guidance, "MAX_BLOCK_CHARS", 7_500)
    (repo / "src.py").write_text("x = 2\n")
    second = _read(repo / "src.py")
    assert "[Project DDD]" in second


def test_lessons_that_did_not_fit_come_back_on_the_next_touch(tmp_path, monkeypatch, _iso):
    """The lessons used to be marked seen before the fit check, so a crowded block hid
    them for the rest of the session (state-machine review)."""
    from core import repo_guidance
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    _bind(monkeypatch, ws, repo)
    _lesson(ws, repo, 1, "always run the migration check")
    monkeypatch.setattr(repo_guidance, "MAX_BLOCK_CHARS", 1000)   # header + DDD line, no lessons
    first = _read(repo / "src.py")
    assert "[Project DDD]" in first and "always run the migration check" not in first
    monkeypatch.setattr(repo_guidance, "MAX_BLOCK_CHARS", 7_500)
    second = _read(repo / "src.py")
    assert "always run the migration check" in second and "[Project DDD]" not in second


def test_one_repo_touched_twice_in_one_call_is_one_block(tmp_path, monkeypatch, _iso):
    import asyncio
    from unittest.mock import MagicMock
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    _bind(monkeypatch, ws, repo)
    (repo / "sub").mkdir()
    hook = rg.create_repo_guidance_hook({"sdk_session_id": "s9"}, "PostToolUse")
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {
        "command": f"cd {repo} && ls && cd {repo / 'sub'} && ls"}, "cwd": str(repo)}, None, MagicMock()))
    text = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert text.count("[Project DDD]") == 1


def test_a_repo_whose_ddd_line_did_not_fit_is_still_one_block_per_call(tmp_path, monkeypatch, _iso):
    import asyncio
    from unittest.mock import MagicMock
    from core import repo_guidance
    ws = _iso["ws"]
    repo = _repo(tmp_path / "proj")
    _bind(monkeypatch, ws, repo)
    (repo / "AGENTS.md").write_text("## Rules\n\n" + "follow the rules. " * 400, encoding="utf-8")
    (repo / "sub").mkdir()
    monkeypatch.setattr(repo_guidance, "MAX_FILE_CHARS", 7_000)   # AGENTS.md takes the budget
    hook = rg.create_repo_guidance_hook({"sdk_session_id": "s10"}, "PostToolUse")
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {
        "command": f"cd {repo} && ls && cd {repo / 'sub'} && ls"}, "cwd": str(repo)}, None, MagicMock()))
    text = (out.get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert text.count(f"guidance — {repo}]") == 1


def test_a_guidance_file_that_did_not_fit_comes_back(tmp_path, monkeypatch, _iso):
    """A file that did not fit (not even as a pointer) used to be marked seen anyway. A
    long repo path makes the pointer itself larger than what is left after AGENTS.md."""
    from core import repo_guidance
    repo = _repo(tmp_path / ("x" * 90) / ("y" * 90) / "proj")
    (repo / "AGENTS.md").write_text("## Rules\n\n" + "short rule. " * 20, encoding="utf-8")
    (repo / "CLAUDE.md").write_text("## Entry\n\nthe claude entry point.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "guidance"], check=True)
    full = _read(repo / "src.py", sid="probe")
    end = full.index(f"--- END {repo / 'AGENTS.md'} ---") + len(f"--- END {repo / 'AGENTS.md'} ---")
    # AGENTS.md (with its note reserve) fits; the CLAUDE.md pointer block does not
    monkeypatch.setattr(repo_guidance, "MAX_BLOCK_CHARS", end + repo_guidance.NOTE_MAX + 20)
    first = _read(repo / "src.py", sid="s11")
    assert "short rule." in first and "CLAUDE.md" not in first
    monkeypatch.setattr(repo_guidance, "MAX_BLOCK_CHARS", 7_500)
    second = _read(repo / "src.py", sid="s11")
    assert "the claude entry point." in second
