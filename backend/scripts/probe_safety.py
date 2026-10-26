#!/usr/bin/env python3
"""probe_safety — product eval probes for the guards that protect the user's work.

Contract: scripts/_probe_kit.py. Subcommands:
  irreversible  with no person able to approve, the command gate refuses deleting a
                repo, force-pushing and changing a repo's visibility, and lets an
                ordinary command such as ``git log`` through
  tab_isolation a tab that needs a slot never evicts a session another open tab owns;
                a session no tab owns can still be evicted (the guard is not "never")
  recovery      a subprocess that dies mid-turn is respawned and the turn resumes; a
                silent spawn is caught by the watchdog, a young one is left alone
"""
from __future__ import annotations

import asyncio
from unittest import mock
from unittest.mock import MagicMock

from scripts._probe_kit import run, verdict

_DENY = ("gh repo delete owner/repo --yes",
         "git push --force origin main",
         "gh repo edit owner/repo --visibility private")
_ALLOW = ("git log --oneline -5", "ls -la")


# ── irreversible ──────────────────────────────────────────────────────────────

def _decision(gate, command: str) -> str:
    out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"},
                           "tool-1", None))
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision") or out.get("decision", "")


def _irreversible_holds() -> tuple[bool, str]:
    from core.security_hooks import create_dangerous_command_gate

    gate = create_dangerous_command_gate({}, "probe-session", MagicMock(),
                                         enable_human_approval=False)
    for command in _DENY:
        if _decision(gate, command) != "deny":
            return False, f"{command!r} ran with no one to approve it"
    for command in _ALLOW:
        if _decision(gate, command) != "approve":
            return False, f"the ordinary command {command!r} was refused"
    return True, ""


def irreversible(negative: bool) -> int:
    def broken():  # the gate recognises nothing as dangerous
        with mock.patch("core.security_hooks._danger_match", return_value=None):
            return _irreversible_holds()
    return verdict("SAFE_IRREVERSIBLE", negative, _irreversible_holds, broken)


# ── tab_isolation ─────────────────────────────────────────────────────────────

def _tab_holds(owned_seen) -> tuple[bool, str]:
    from scripts import fault_inject_dual_tab as dt

    async def evict(router, owned, force):
        with mock.patch("routers.settings.owned_session_ids", side_effect=lambda: owned_seen(owned)):
            return await router._evict_idle(exclude=dt._requester(router), force=force)

    router = dt._make_router()
    tab_a = dt._add_idle(router, "tab-A-session")
    if asyncio.run(evict(router, {"tab-A-session"}, True)) is not False or tab_a.kill.await_count:
        return False, "a session another tab owns was evicted"
    router = dt._make_router()
    orphan = dt._add_idle(router, "orphan-session")
    if asyncio.run(evict(router, set(), False)) is not True or not orphan.kill.await_count:
        return False, "a session no tab owns could not be evicted"
    return True, ""


def tab_isolation(negative: bool) -> int:
    return verdict("SAFE_TAB_ISOLATION", negative, lambda: _tab_holds(lambda owned: owned),
                   lambda: _tab_holds(lambda owned: set()))  # ownership never seen


# ── recovery ──────────────────────────────────────────────────────────────────

def _recovery_holds() -> tuple[bool, str]:
    from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
    from scripts import fault_inject_dumb_spawn as ds
    from scripts import fault_inject_recovery as fr

    ok, msg = asyncio.run(fr._run())
    if not ok:
        return False, f"a dead subprocess was not recovered: {msg}"
    stalled = ds._make_dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS + 5)
    ds._drive_watchdog(stalled)
    if not stalled.force_unstick_streaming.await_count:
        return False, "a silent spawn past the limit was not caught"
    young = ds._make_dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS - 30)
    ds._drive_watchdog(young)
    if young.force_unstick_streaming.await_count:
        return False, "a spawn still inside its window was killed"
    return True, ""


def recovery(negative: bool) -> int:
    async def gives_up(self, **kwargs):  # a recovery that aborts at once
        yield {"_abort": True}

    def broken():
        with mock.patch("core.retry_manager.RetryManager._retry_with_resume", gives_up):
            return _recovery_holds()
    return verdict("SAFE_RECOVERY", negative, _recovery_holds, broken)


if __name__ == "__main__":
    raise SystemExit(run({"irreversible": irreversible, "tab_isolation": tab_isolation,
                          "recovery": recovery}))
