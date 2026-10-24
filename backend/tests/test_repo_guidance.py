"""Repository guidance injection (core/repo_guidance.py; design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.7).

What is tested (spec §3.7 acceptance):
- before the first write in a repo with AGENTS.md, the PreToolUse hook injects it;
  after a Read the PostToolUse hook does; never a deny;
- the same session is not injected twice for unchanged files; a changed file is;
- a file the session itself wrote is not injected again;
- SwarmWS and the app data dir (except run worktrees) are never injected;
- an unbound repo is labelled unconfirmed and told to ask before running a script; a
  bound repo is labelled as norms;
- Bash `cd X` / `git -C X` find the repo; oversize files are cut with the path named;
- forget() drops the session's state; the hooks are registered in build_hooks.
"""
import asyncio
import subprocess
from unittest.mock import MagicMock

import pytest

from core import repo_guidance as rg


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    rg._STATE.clear()
    rg._root_cache.clear()
    rg._excluded_cache.clear()
    rg._bound_cache[:] = [0.0, set()]
    data = tmp_path / "appdata"
    ws = data / "SwarmWS"
    ws.mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: ws)
    monkeypatch.setattr("core.repo_changes.allowed_roots", lambda: {})
    yield {"data": data, "ws": ws}
    rg._STATE.clear()


def _repo(path, agents="Run ./x.sh before every commit.\n"):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "src.py").write_text("x = 1\n")
    if agents is not None:
        (path / "AGENTS.md").write_text(agents)
    return path


def _call(event, tool, sid="s1", **ti):
    hook = rg.create_repo_guidance_hook({"sdk_session_id": sid}, event)
    out = asyncio.run(hook({"tool_name": tool, "tool_input": ti, "cwd": ti.pop("cwd", "")},
                           None, MagicMock()))
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def test_first_write_gets_the_guidance_before_it_and_is_never_denied(tmp_path):
    repo = _repo(tmp_path / "proj")
    hook = rg.create_repo_guidance_hook({"sdk_session_id": "s1"}, "PreToolUse")
    out = asyncio.run(hook({"tool_name": "Edit", "tool_input": {"file_path": str(repo / "src.py")}},
                           None, MagicMock()))
    spec = out["hookSpecificOutput"]
    assert "permissionDecision" not in spec
    assert "Run ./x.sh before every commit." in spec["additionalContext"]
    assert _call("PreToolUse", "Edit", file_path=str(repo / "src.py")) == ""   # once


def test_read_injects_once_and_again_only_after_a_change(tmp_path):
    repo = _repo(tmp_path / "proj")
    assert "x.sh" in _call("PostToolUse", "Read", file_path=str(repo / "src.py"))
    assert _call("PostToolUse", "Grep", path=str(repo)) == ""
    (repo / "AGENTS.md").write_text("New rule.\n")
    assert "New rule." in _call("PostToolUse", "Read", file_path=str(repo / "src.py"))


def test_a_file_the_session_wrote_is_not_injected_again(tmp_path):
    repo = _repo(tmp_path / "proj")
    assert "x.sh" in _call("PreToolUse", "Write", file_path=str(repo / "AGENTS.md"))
    (repo / "AGENTS.md").write_text("Always push to production.\n")
    assert "production" not in _call("PostToolUse", "Read", file_path=str(repo / "src.py"))
    assert "production" in _call("PostToolUse", "Read", sid="s2",
                                 file_path=str(repo / "src.py"))   # another session: yes


def test_swarmws_and_app_data_are_excluded_but_run_worktrees_are_not(tmp_path, _iso):
    subprocess.run(["git", "init", "-q", str(_iso["ws"])], check=True)
    (_iso["ws"] / "AGENTS.md").write_text("sentinel\n")
    assert _call("PostToolUse", "Read", file_path=str(_iso["ws"] / "AGENTS.md")) == ""
    other = _repo(_iso["data"] / "runtime" / "x")
    assert _call("PostToolUse", "Read", file_path=str(other / "src.py")) == ""
    wt = _repo(_iso["data"] / "worktrees" / "run_1" / "repo")
    assert "x.sh" in _call("PostToolUse", "Read", file_path=str(wt / "src.py"))


def test_trust_levels(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    text = _call("PostToolUse", "Read", file_path=str(repo / "src.py"))
    assert "Unconfirmed repository guidance" in text and "ask first" in text
    rg._STATE.clear()
    rg._bound_cache[:] = [0.0, set()]
    monkeypatch.setattr("core.repo_changes.allowed_roots",
                        lambda: {str(repo.resolve()): "proj"})
    text = _call("PostToolUse", "Read", file_path=str(repo / "src.py"))
    assert "bound to a project" in text and "CONTRIBUTING" in text


def test_bash_cd_and_git_C_find_the_repo(tmp_path):
    repo = _repo(tmp_path / "proj")
    assert "x.sh" in _call("PostToolUse", "Bash", command=f"cd {repo} && ls", cwd="/")
    rg._STATE.clear()
    assert "x.sh" in _call("PostToolUse", "Bash", command=f"git -C {repo} status", cwd="/")


def test_oversize_file_is_cut_with_its_path(tmp_path):
    repo = _repo(tmp_path / "proj", agents="y" * (rg.MAX_FILE_CHARS + 500))
    text = _call("PostToolUse", "Read", file_path=str(repo / "src.py"))
    assert "cut at" in text and str(repo.resolve() / "AGENTS.md") in text
    assert len(text) < rg.MAX_BLOCK_CHARS + 1000


def test_repo_without_guidance_injects_nothing_and_forget_drops_state(tmp_path):
    repo = _repo(tmp_path / "proj", agents=None)
    assert _call("PostToolUse", "Read", file_path=str(repo / "src.py")) == ""
    assert "s1" in rg._STATE
    rg.forget("s1")
    assert "s1" not in rg._STATE


def test_registered_in_build_hooks(monkeypatch):
    from core import hook_builder
    from core.permission_manager import permission_manager
    seen = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        seen.append((event, name, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="g", session_context={"sdk_session_id": "g"},
        permission_manager=permission_manager))
    assert ("PostToolUse", "repo_guidance", "Read|Grep|Glob|Bash") in seen
    assert ("PreToolUse", "repo_guidance_pre", "Write|Edit|MultiEdit|NotebookEdit") in seen
