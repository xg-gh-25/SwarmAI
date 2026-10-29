"""Native work is never blocked (design 2026-10-24-swarmai-usable-by-anyone §3.4, §5.2).

What is tested:
- build_hooks registers none of the removed native-blocking gates (direct_commit_gate,
  adversarial_commit_gate, pytest_command_guard), and every hook it registers has a §3.4
  class in GATE_CLASS: a closed set, so a new hook without a class fails (closing guards
  design 2026-10-25 §3.1 K2), and so does a class entry for a hook no longer registered.
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

REMOVED = ("direct_commit_gate", "adversarial_commit_gate", "pytest_command_guard",
           "pipeline_test_gate")

#: §3.4 of the usable-by-anyone design, one class per registered hook. Before adding a
#: hook to build_hooks, put it here: the class is the answer to P3's question "what does
#: this protect, for every user?". Only SAFETY, SESSION and RUN entries may refuse a
#: call; ADVISORY and OBSERVE hooks inform or record.
SAFETY = "safety"            # dangerous / irreversible / off-machine actions, a human's answer
SESSION = "session"          # the session itself: no hang, no runaway token burn
RUN = "run"                  # the integrity of a pipeline run the user started
ADVISORY = "advisory"        # memory / DDD / repo guidance: reminders, never a refusal
OBSERVE = "observe"          # logging, capture, tracking, compaction continuity
GATE_CLASS = {
    "dangerous_command_gate": SAFETY, "external_approval_gate": SAFETY,
    "ask_question_gate": SAFETY, "bash_syntax_guard": SAFETY, "skill_access_checker": SAFETY,
    "symlink_target_guard": SAFETY,
    "background_command_guard": SESSION, "image_read_dedup_guard": SESSION,
    "run_owner_gate": RUN, "run_owner_gate_approved": RUN, "run_amend_gate": RUN,
    "active_run_commit_gate": RUN, "session_link_gate": RUN, "audit_marker_write_gate": RUN,
    "expert_type_gate": RUN, "expert_budget_gate": RUN, "worktree_write_gate": RUN,
    "main_dirty_reporter": RUN, "v1_reflect_trigger": RUN, "pipeline_run_mapping": RUN,
    "v1_review_handout_tracker": RUN,
    # a Project Agent session only: the gates of the definition the person confirmed
    "brain_gate": RUN,
    "governance_file_gate": ADVISORY, "memory_edit_guard": ADVISORY,
    "persist_skill_tracker": ADVISORY, "persist_skill_tracker_reset": ADVISORY,
    "repo_guidance": ADVISORY, "repo_guidance_pre": ADVISORY, "shared_checkout": ADVISORY,
    "shared_checkout_pre": ADVISORY, "enforcement_injector": ADVISORY,
    "pre_tool_logger": OBSERVE, "post_compact_summary": OBSERVE, "pre_compact_flag": OBSERVE,
    "post_compact_injection": OBSERVE, "notification_capture": OBSERVE, "stop_capture": OBSERVE,
    "correction_capture": OBSERVE, "error_pattern_detector": OBSERVE,
    "failure_tracker_reset": OBSERVE, "file_tracker": OBSERVE, "session_checkpoint": OBSERVE,
    "subagent_capture": OBSERVE, "agent_tool_audit": OBSERVE,
    "user_correction_detector": OBSERVE, "high_signal_capture": OBSERVE,
    "observation_recorder": OBSERVE, "observation_completer": OBSERVE,
    "cdf_usage_observer": OBSERVE,
}


def _registered_names(monkeypatch, *, agent_config=None, enable_skills=False):
    from core import hook_builder
    from core.permission_manager import permission_manager
    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        recorded.extend(getattr(hook, "members", (name,)))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    asyncio.run(hook_builder.build_hooks(
        agent_config=agent_config or {"id": "default"}, enable_skills=enable_skills,
        enable_mcp=False, resume_session_id="native-sess",
        session_context={"sdk_session_id": "native-sess"},
        permission_manager=permission_manager,
    ))
    return recorded


def _every_registered_name(monkeypatch) -> set[str]:
    """Union over the configurations that change what build_hooks registers: the
    default, and a restricted-skills agent (adds skill_access_checker)."""
    names = set(_registered_names(monkeypatch))
    names |= set(_registered_names(
        monkeypatch, enable_skills=True,
        agent_config={"id": "default", "global_user_mode": False, "allow_all_skills": False}))
    return names


def test_removed_gates_are_not_registered(monkeypatch):
    names = _every_registered_name(monkeypatch)
    for n in REMOVED:
        assert n not in names, n
        assert n not in GATE_CLASS, n


def test_every_registered_hook_has_a_class_and_every_class_entry_is_registered(monkeypatch):
    names = _every_registered_name(monkeypatch)
    unclassified = sorted(names - GATE_CLASS.keys())
    assert not unclassified, (
        f"hooks without a usable-by-anyone §3.4 class: {unclassified}. Add each to "
        "GATE_CLASS; a refusing hook must protect every user (P3), never one repo's habit.")
    gone = sorted(GATE_CLASS.keys() - names)
    assert not gone, f"GATE_CLASS names hooks build_hooks no longer registers: {gone}"


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
