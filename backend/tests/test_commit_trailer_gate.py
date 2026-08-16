"""Tests for the commit-trailer identity gate (scripts/check_commit_trailers.py).

WHY THIS FILE EXISTS
--------------------
The trailer rule exists because ``.git/hooks/prepare-commit-msg`` — which rewrites an
SDK ``Co-Authored-By: Claude`` trailer into the project's ``Swarm`` identity — NEVER
RUNS: ``core.hooksPath`` points at the corporate git-defender hook set, and a hooksPath
override REPLACES ``.git/hooks`` rather than merging with it. So the trailer rule was
unenforced (22 of the last 80 commits carry none).

Enforcement is at COMMIT time via the ``create_commit_trailer_gate()`` PreToolUse hook
(PREVENTION). The CI DETECTION half (scripts/check_commit_trailers.py wired into ci.yml)
was REMOVED 2026-08-16: it enforced a cosmetic signature, could not self-enforce (the
shadowed hook above), and required 4 manual amnesties in 3 days — recurring toil for
zero quality signal. This file therefore no longer tests CI wiring; it tests the live
PreToolUse gate, which per INV-5 needs a test that ACTUALLY ENTERS it (six guards in
this codebase shipped without ever executing).

Covered:
  - classify() on each real shape (ok / missing / wrong-identity), including the
    Claude/Anthropic identity the project rule names explicitly — retained because the
    script is kept for optional local/manual use;
  - the PreToolUse gate (prevention) firing on every inline-commit shape and failing
    OPEN on every opaque message source.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "check_commit_trailers.py"


def _load():
    spec = importlib.util.spec_from_file_location("cct", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cct():
    assert _SCRIPT.is_file(), f"gate script missing: {_SCRIPT}"
    return _load()


def test_classify_accepts_the_project_identity(cct):
    body = "Some body text\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>\n"
    assert cct.classify(body) == "ok"


def test_classify_flags_a_missing_trailer(cct):
    assert cct.classify("Just a body, no trailer at all\n") == "missing"
    assert cct.classify("") == "missing"


def test_classify_flags_the_forbidden_claude_identity(cct):
    """The project rule: 'Never use Claude/Anthropic identity in commit trailers'."""
    for line in (
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "Co-authored-by: Claude Code <noreply@anthropic.com>",
    ):
        assert cct.classify(f"body\n\n{line}\n") == "wrong-identity", line


def test_classify_flags_any_other_co_author_identity(cct):
    """A trailer that is present but is not the project identity is still a violation —
    otherwise 'add any co-author' would satisfy an identity rule."""
    body = "body\n\nCo-Authored-By: Someone Else <someone@example.com>\n"
    assert cct.classify(body) == "wrong-identity"


def test_required_trailer_matches_the_documented_rule(cct):
    """AGENTS.md pins the exact string; a typo here would silently accept nothing."""
    assert cct.REQUIRED_TRAILER == "Co-Authored-By: Swarm <swarm@swarmai.dev>"


# ---------------------------------------------------------------------------
# PreToolUse commit-trailer gate — PREVENTION (the CI DETECTION half was removed
# 2026-08-16; see module docstring). This is now the sole live enforcer.
# ---------------------------------------------------------------------------
# create_commit_trailer_gate() denies a trailer-less commit at commit time, when
# the fix is one re-run. Per INV-5 every test below ACTUALLY INVOKES the gate.
import asyncio


def _run_gate(command: str, tool_name: str = "Bash", cwd: str = "/repo"):
    """Invoke the real gate on one Bash command; return its decision dict."""
    from core.security_hooks import create_commit_trailer_gate

    gate = create_commit_trailer_gate()
    return asyncio.run(gate(
        {"tool_name": tool_name, "tool_input": {"command": command}, "cwd": cwd},
        None,
        None,
    ))


def _is_deny(result) -> bool:
    return (result.get("hookSpecificOutput", {}) or {}).get("permissionDecision") == "deny"


_GOOD = "Co-Authored-By: Swarm <swarm@swarmai.dev>"


def test_gate_denies_inline_commit_without_the_trailer():
    """The real violation class: 3/3 of the 2026-08-11 violations were `missing`."""
    assert _is_deny(_run_gate('git commit -m "fix(x): do a thing"'))


def test_gate_approves_inline_commit_carrying_the_trailer():
    assert not _is_deny(_run_gate(f'git commit -m "fix(x): thing\n\n{_GOOD}"'))


def test_gate_denies_the_forbidden_claude_identity():
    """A trailer that is present but is the SDK identity is still a rule violation."""
    body = "Co-Authored-By: Claude <noreply@anthropic.com>"
    assert _is_deny(_run_gate(f'git commit -m "fix: thing\n\n{body}"'))


def test_gate_denies_claude_identity_even_alongside_the_correct_one():
    """Both present → still DENY. The SDK appending its identity next to ours must
    not be laundered into a pass by the correct line's mere presence."""
    both = f"{_GOOD}\nCo-Authored-By: Claude <noreply@anthropic.com>"
    assert _is_deny(_run_gate(f'git commit -m "fix: thing\n\n{both}"'))


def test_gate_detects_the_combined_short_flag_form():
    """`git commit -am` is inline too — a gate that only knew `-m` would miss it."""
    assert _is_deny(_run_gate('git commit -am "fix: thing"'))


def test_gate_detects_the_heredoc_stdin_form():
    """`-F -` puts the message body IN the command string, so it IS readable."""
    cmd = "git commit -F - <<'MSG'\nfix: thing\nMSG"
    assert _is_deny(_run_gate(cmd))
    assert not _is_deny(_run_gate(f"git commit -F - <<'MSG'\nfix: thing\n\n{_GOOD}\nMSG"))


# ── fail-OPEN contract: the gate must never block a message it cannot read ──
# A false block here is worse than the violation it prevents, because CI already
# catches the violation. Each case below is a real git message source.
@pytest.mark.parametrize("command", [
    "git commit -F /tmp/msg.txt",          # message on disk — not in the command
    "git commit --file=/tmp/msg.txt",      # long form of the same
    "git commit --amend --no-edit",        # reuses the existing message
    "git commit -C HEAD@{1}",              # copies another commit's message
    "git commit --reuse-message=abc123",   # long form of -C
    "git commit --fixup=abc123",           # message generated from another commit
    "git commit --squash=abc123",
    "git commit",                          # bare editor form
    "git commit -a",                        # editor form with -a
])
def test_gate_approves_every_opaque_message_source(command):
    assert not _is_deny(_run_gate(command)), f"must fail OPEN for: {command}"


def test_amend_alone_is_not_mistaken_for_an_inline_message():
    """Regex guard: `--amend` ends in no `m`-token but contains 'm'. An earlier naive
    `-[A-Za-z]*m` without the (?:^|\\s) anchor would match inside `--amend` and
    false-block every amend."""
    from core.security_hooks import _commit_message_is_inline
    assert _commit_message_is_inline("git commit --amend --no-edit") is False
    assert _commit_message_is_inline('git commit --amend -m "x"') is True


def test_gate_ignores_non_bash_tools_and_non_commit_commands():
    assert not _is_deny(_run_gate('git commit -m "no trailer"', tool_name="Write"))
    assert not _is_deny(_run_gate("git status"))
    assert not _is_deny(_run_gate('git log --format=%s -m "not a commit"'))


def test_force_env_is_a_sanctioned_bypass(monkeypatch):
    monkeypatch.setenv("SWARM_TRAILER_GATE_FORCE", "1")
    assert not _is_deny(_run_gate('git commit -m "deliberate, no trailer"'))


def test_deny_reason_tells_the_agent_the_exact_line_to_add():
    """A deny the agent cannot act on just becomes a retry loop."""
    reason = _run_gate('git commit -m "x"')["hookSpecificOutput"]["permissionDecisionReason"]
    assert _GOOD in reason, "the reason must contain the literal trailer to append"
    assert "re-run" in reason.lower()


def test_required_trailer_is_the_same_string_the_ci_gate_enforces(cct):
    """Two enforcers, one rule — a drift here means the gate passes what CI fails."""
    from core.security_hooks import _REQUIRED_TRAILER
    assert _REQUIRED_TRAILER == cct.REQUIRED_TRAILER


def test_gate_is_actually_registered_in_the_hook_chain():
    """This repo has shipped six guards that never executed (_get_session_router
    NameError, self._pid, the inert reconciliation endpoint, an eslint rule in no CI
    step...). An unregistered gate is prose, so pin the wiring."""
    src = (_REPO / "backend" / "core" / "hook_builder.py").read_text(encoding="utf-8")
    assert "create_commit_trailer_gate" in src, "gate not imported in hook_builder"
    assert re.search(
        r'registry\.register\(\s*\n?\s*"PreToolUse",\s*create_commit_trailer_gate\(\)',
        src,
    ), "commit_trailer_gate is not registered as a PreToolUse hook"
    assert re.search(r'"commit_trailer_gate",\s*matcher="Bash"', src), (
        "the gate must be Bash-scoped — an unscoped PreToolUse hook runs on every tool"
    )
