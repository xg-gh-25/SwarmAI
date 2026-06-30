"""Prewarm sessions must NOT fire post-session lifecycle hooks.

A pre-warmed session (id prefix ``prewarm-``) is an empty-shell subprocess
spawned ahead of a channel's first message — it carries no conversation. Yet
every unadopted prewarm unit, once it hits the idle/TTL/orphan-reaper paths,
funnelled through ``LifecycleManager.enqueue_hooks`` and fired the full ~11-hook
post-session chain (~6.5s: workspace_auto_commit / evolution_maintenance /
context_health / distillation / …). 24h of prod logs: 32 prewarm spawned, 0
adopted, 28 orphan-reaped — every one paid the 6.5s for nothing.

These tests pin the fix: ``enqueue_hooks`` is a no-op for a ``prewarm-`` session
id, fires normally for a real session id, and — because adoption re-keys the
unit's ``session_id`` to a real id — fires normally again post-adoption.

Methodology: drive the REAL ``LifecycleManager.enqueue_hooks`` with a recording
stub executor (no mock of the function under change). Mutation check: removing
the guard makes ``test_prewarm_session_skips_hooks`` go RED.
"""
from __future__ import annotations

from core.lifecycle_manager import LifecycleManager
from core.session_router import PREWARM_SESSION_PREFIX
from core.session_hooks import HookContext


class _RecordingExecutor:
    """Stub BackgroundHookExecutor that records every fire() call."""

    def __init__(self) -> None:
        self.fired: list[str] = []

    def fire(self, context: HookContext, skip_hooks=None) -> None:
        self.fired.append(context.session_id)


def _ctx(session_id: str) -> HookContext:
    return HookContext(
        session_id=session_id,
        agent_id="agent-default",
        message_count=0,
        session_start_time="",
        session_title="test",
    )


def _make_lifecycle(executor) -> LifecycleManager:
    # router is unused by enqueue_hooks; None is sufficient for this unit.
    return LifecycleManager(router=None, hook_executor=executor)


def test_prewarm_session_skips_hooks():
    """AC1: a prewarm-* session id must NOT enqueue any lifecycle hook.

    Mutation anchor: delete the prewarm guard in enqueue_hooks → this goes RED.
    """
    ex = _RecordingExecutor()
    lm = _make_lifecycle(ex)
    lm.enqueue_hooks(_ctx(f"{PREWARM_SESSION_PREFIX}abc-123"))
    assert ex.fired == [], (
        "prewarm session must not fire hooks, but fired: %r" % ex.fired
    )


def test_real_session_fires_hooks():
    """AC2: a real (non-prewarm) session id fires hooks exactly once — unchanged."""
    ex = _RecordingExecutor()
    lm = _make_lifecycle(ex)
    real_id = "f790f427-899e-4dd9-a5c2-3c14079014a0"
    lm.enqueue_hooks(_ctx(real_id))
    assert ex.fired == [real_id], (
        "real session must fire hooks once, got: %r" % ex.fired
    )


def test_adopted_session_resumes_hooks():
    """AC3: after adoption re-keys the id to a real id, hooks fire normally.

    Adoption (adopt_prewarmed_unit) sets unit.session_id = real_session_id, and
    _build_hook_context reads unit.session_id live — so the very same unit, once
    adopted, produces a HookContext with a real id and is NOT skipped.
    """
    ex = _RecordingExecutor()
    lm = _make_lifecycle(ex)
    real_id = "11111111-2222-3333-4444-555555555555"
    # Pre-adoption: prewarm id is skipped.
    lm.enqueue_hooks(_ctx(f"{PREWARM_SESSION_PREFIX}seed"))
    # Post-adoption: same unit now carries the real id → fires.
    lm.enqueue_hooks(_ctx(real_id))
    assert ex.fired == [real_id], (
        "post-adoption real id must fire hooks, got: %r" % ex.fired
    )


def test_empty_session_id_does_not_crash():
    """Edge: an empty/None-ish session id must not crash the guard."""
    ex = _RecordingExecutor()
    lm = _make_lifecycle(ex)
    lm.enqueue_hooks(_ctx(""))  # empty string — startswith is safe, fires (real)
    assert ex.fired == [""]
