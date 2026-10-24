"""Native work is never blocked (design 2026-10-24-swarmai-usable-by-anyone §3.4, §5.2).

What is tested:
- build_hooks registers none of the removed native-blocking gates (direct_commit_gate,
  adversarial_commit_gate, pytest_command_guard), and the gates that stay are still there.
- worktree_write_gate: a session with no run may write a code file in a project repo's
  main checkout (old rule 4 denied it) but still may not write into a run's worktree.
- the commit gate group lets a large native source commit through in a session with no
  run and no review sub-agent.
- the UserPromptSubmit enforcement injector no longer says adversarial review is
  mandatory when the user asks to "just do it".
Methodology: real build_hooks / real gate factories; git repos in tmp_path.
"""
import asyncio
import subprocess
from unittest.mock import MagicMock

import pytest

REMOVED = ("direct_commit_gate", "adversarial_commit_gate", "pytest_command_guard")
KEPT = ("pre_tool_logger", "run_amend_gate", "active_run_commit_gate", "pipeline_test_gate",
        "session_link_gate", "audit_marker_write_gate", "dangerous_command_gate",
        "external_approval_gate", "ask_question_gate", "governance_file_gate")


def _registered_names(monkeypatch):
    from core import hook_builder
    from core.permission_manager import permission_manager
    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.extend(getattr(hook, "members", (name,)))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="native-sess", session_context={"sdk_session_id": "native-sess"},
        permission_manager=permission_manager,
    ))
    return recorded


def test_removed_gates_are_not_registered_and_kept_gates_are(monkeypatch):
    names = _registered_names(monkeypatch)
    for n in REMOVED:
        assert n not in names, n
    for n in KEPT:
        assert n in names, n


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"})


def test_no_run_session_may_write_code_in_a_main_checkout(tmp_path, monkeypatch):
    from core import worktree_write_gate as wg
    from core import run_worktree
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    from core import security_hooks
    monkeypatch.setattr(run_worktree, "run_for_path", lambda p: None)
    # make the repo look like a workspace project's code repo: old rule 4 denied exactly this
    monkeypatch.setattr(run_worktree, "repo_for_write", lambda p, root: repo, raising=False)
    monkeypatch.setattr(security_hooks, "_project_for_repo", lambda root: "Proj", raising=False)
    v = wg.judge({"tool_name": "Write", "tool_input": {"file_path": str(repo / "a.py")},
                  "cwd": str(repo)}, None, no_run=wg._NoRun(None))
    assert v is None


def test_no_run_session_still_may_not_write_into_a_run_worktree(tmp_path, monkeypatch):
    from core import worktree_write_gate as wg
    from core import run_worktree
    monkeypatch.setattr(run_worktree, "run_for_path", lambda p: ("run_x", str(tmp_path)))
    v = wg.judge({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.py")},
                  "cwd": str(tmp_path)}, None, no_run=wg._NoRun(None))
    assert v["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_large_native_commit_without_review_is_not_denied(tmp_path, monkeypatch):
    from core import hook_builder
    from core.permission_manager import permission_manager
    groups = {}
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        groups[name] = hook
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="native-sess", session_context={"sdk_session_id": "native-sess"},
        permission_manager=permission_manager,
    ))
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "big.py").write_text("".join(f"x{i} = {i}\n" for i in range(200)))
    _git(repo, "add", "big.py")
    out = asyncio.run(groups["commit_gate"](
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m big"}, "cwd": str(repo)},
        None, MagicMock()))
    decision = (out or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    assert decision != "deny", out


@pytest.mark.parametrize("msg", ["直接做 别走 pipeline 帮我改一下", "just do it, skip the ceremony"])
def test_just_do_it_gets_no_mandatory_review_reminder(msg):
    from core.runtime_hooks import create_enforcement_injector
    out = asyncio.run(create_enforcement_injector()({"prompt": msg}, None, MagicMock()))
    ac = (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "adversarial" not in ac.lower() and "mandatory" not in ac.lower()
